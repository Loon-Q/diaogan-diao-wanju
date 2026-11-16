"""Optional official SAM2 single-frame matting; core renderer needs no PyTorch."""
import argparse,hashlib,json,os,ssl,subprocess,sys,urllib.request
from pathlib import Path

SKILL=Path(__file__).resolve().parents[1]
REVISION='2b90b9f5ceec907a1c18123530e92e794ad901a4'
WEIGHT_URL='https://huggingface.co/facebook/sam2.1-hiera-small/resolve/main/sam2.1_hiera_small.pt'
WEIGHT_SHA='6d1aa6f30de5c92224f8172114de081d104bbd23dd9dc5c58996f0cad5dc4d38'
ENV=SKILL/'.sam2-env'
PY=ENV/('Scripts/python.exe' if os.name=='nt' else 'bin/python')

def checkpoint(explicit=None):
    if explicit:return Path(explicit).expanduser().resolve()
    codex=Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex')))
    folder=codex/'fishing-gif/models';folder.mkdir(parents=True,exist_ok=True)
    path=folder/'sam2.1_hiera_small.pt'
    if not path.exists():
        import certifi
        part=path.with_suffix('.download')
        with urllib.request.urlopen(WEIGHT_URL,context=ssl.create_default_context(cafile=certifi.where()),timeout=60) as source,part.open('wb') as target:
            while True:
                block=source.read(1024*1024)
                if not block:break
                target.write(block)
        if hashlib.sha256(part.read_bytes()).hexdigest()!=WEIGHT_SHA:
            part.unlink();raise ValueError('Downloaded checkpoint hash mismatch.')
        part.replace(path)
    if hashlib.sha256(path.read_bytes()).hexdigest()!=WEIGHT_SHA:
        raise ValueError('Cached SAM2 checkpoint hash mismatch.')
    return path

def setup():
    if not PY.exists():subprocess.run([sys.executable,'-m','venv',str(ENV)],check=True)
    env={**os.environ,'SAM2_BUILD_CUDA':'0'}
    subprocess.run([str(PY),'-m','pip','install','torch>=2.5.1','torchvision>=0.20.1','numpy>=1.26,<3','Pillow>=10,<13','opencv-python-headless>=4.8,<6','setuptools','wheel','hydra-core','iopath','tqdm','certifi'],check=True,env=env)
    subprocess.run([str(PY),'-m','pip','install','--no-build-isolation',f'git+https://github.com/facebookresearch/sam2.git@{REVISION}'],check=True,env=env)
    subprocess.run([str(PY),str(Path(__file__).resolve()),'download','--worker'],check=True,env=env)

def segment(args):
    import cv2,numpy as np,torch
    from PIL import Image
    from sam2.build_sam import build_sam2
    from sam2.sam2_image_predictor import SAM2ImagePredictor
    project=Path(args.project).resolve();root=project.parent
    c=json.loads(project.read_text());cfg=c['matte'].get('sam2',{})
    objects=cfg.get('objects') or [cfg]
    if not objects or not any(o.get('positive') or o.get('box') for o in objects):
        raise ValueError('Set matte.sam2.objects with inspected box/positive/negative points on the pickup frame.')
    im=np.array(Image.open(root/c['media']['pickup']).convert('RGB'))
    torch.set_num_threads(min(6,os.cpu_count() or 1))
    model=build_sam2('configs/sam2.1/sam2.1_hiera_s.yaml',str(checkpoint(args.checkpoint)),device=args.device,apply_postprocessing=False)
    predictor=SAM2ImagePredictor(model);mask=np.zeros(im.shape[:2],np.uint8)
    with torch.inference_mode():
        predictor.set_image(im)
        for obj in objects:
            pos=obj.get('positive',[]);neg=obj.get('negative',[])
            points=np.array(pos+neg,np.float32) if pos or neg else None
            labels=np.array([1]*len(pos)+[0]*len(neg),np.int32) if points is not None else None
            result,_,_=predictor.predict(point_coords=points,point_labels=labels,box=np.array(obj['box'],np.float32) if obj.get('box') else None,multimask_output=False)
            local=result[0].astype(np.uint8)
            if obj.get('clip'):
                x0,y0,x1,y1=map(int,obj['clip']);roi=np.zeros_like(local);roi[y0:y1,x0:x1]=1;local*=roi
            if obj.get('subtract'):mask[local>0]=0
            else:mask=np.maximum(mask,local)
    inside=cv2.distanceTransform(mask,cv2.DIST_L2,5);outside=cv2.distanceTransform(1-mask,cv2.DIST_L2,5)
    alpha=np.uint8(np.clip(.5+(inside-outside)/2.3,0,1)*255+.5)
    output=root/'assets/sam-mask.png';Image.fromarray(alpha).save(output)
    c['matte']['mask_file']='assets/sam-mask.png';c['matte']['feather']=0
    temporary=project.with_suffix('.json.tmp');temporary.write_text(json.dumps(c,ensure_ascii=False,indent=2)+'\n');temporary.replace(project)
    print(json.dumps({'mask':str(output),'next':'Run fishing_gif.py matte, then inspect checkerboard.'}))

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=['setup','download','segment'])
    p.add_argument('project',nargs='?');p.add_argument('--device',choices=['cpu','mps','cuda'],default='cpu')
    p.add_argument('--checkpoint');p.add_argument('--worker',action='store_true');args=p.parse_args()
    if args.command=='setup':setup();return
    try:import sam2,torch
    except ImportError:
        if PY.exists() and not args.worker:
            subprocess.run([str(PY),str(Path(__file__).resolve()),*sys.argv[1:],'--worker'],check=True);return
        raise SystemExit('Optional SAM2 is not installed. Run this script with setup, or use an inspected mask/polygons through the core matte command.')
    if args.command=='download':print(checkpoint(args.checkpoint));return
    if not args.project:p.error('segment requires a project.json')
    segment(args)

if __name__=='__main__':main()
