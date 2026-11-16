"""Native footage until takeoff, articulated single-hand rig, complete quick exit."""
from pathlib import Path
import math
import cv2
import numpy as np
from PIL import Image

SKILL=Path(__file__).resolve().parents[1]
GRIP=np.array([900.,930.],np.float32)
TIP=np.array([103.,166.],np.float32)
CUFF=np.array([702.,680.],np.float32)

def smooth(a,b,t):
    if b<=a: raise ValueError('Animation event times must increase.')
    q=float(np.clip((t-a)/(b-a),0,1));return q*q*(3-2*q)

def rotation(a):
    a=math.radians(a)
    return np.array([[math.cos(a),-math.sin(a)],[math.sin(a),math.cos(a)]],np.float32)

def matrix(origin,dest,angle=0,scale=1):
    r=rotation(angle)*scale
    return np.column_stack([r,np.asarray(dest)-r@np.asarray(origin)]).astype(np.float32)

def rgb(path):return np.array(Image.open(path).convert('RGB'))
def rgba(path):return np.array(Image.open(path).convert('RGBA'))
def premul(im):
    a=im.astype(np.float32)/255;a[...,:3]*=a[...,3:4];return a
def over(bg,fg):
    return np.rint(np.clip(bg*(1-fg[...,3:4])+fg[...,:3]*255,0,255)).astype(np.uint8)

class Scene:
    def __init__(self,root,c):
        self.root,self.c,self.t=Path(root),c,c['timing']
        self.w,self.h=c['canvas']['width'],c['canvas']['height']
        self.k=min(self.w/720,self.h/1280)
        self.last=rgb(self.root/c['media']['pickup'])
        self.bot_raw=rgba(self.root/c['matte']['output']);self.bot=premul(self.bot_raw)
        self.alpha=self.bot_raw[...,3]
        if self.last.shape!=(self.h,self.w,3) or self.bot.shape!=(self.h,self.w,4):
            raise ValueError('Pickup, matte and canvas dimensions must match.')
        if c['subject'].get('anchor') is None:raise ValueError('Set a real subject.anchor on the pickup frame.')
        self.anchor=np.array(c['subject']['anchor'],np.float32)
        x,y=np.round(self.anchor).astype(int)
        if not (0<=x<self.w and 0<=y<self.h and self.alpha[y,x]>80):
            raise ValueError('Contact point is not on the subject matte.')
        self.mode=c['subject'].get('motion','lift')
        if self.mode not in ('lift','drag'):raise ValueError('subject.motion must be lift or drag.')
        t=self.t
        if not (0<=t['enter_start']<t['enter_end']<=t['line_start']<t['contact']<=t['pickup']==t['lift']<t['exit']<t['end']):
            raise ValueError('Use ordered entry/drop/contact/pickup/exit times; pickup equals lift.')
        cap=cv2.VideoCapture(str(self.root/c['media']['native']));self.frames=[]
        while True:
            ok,f=cap.read()
            if not ok:break
            self.frames.append(cv2.cvtColor(f,cv2.COLOR_BGR2RGB))
        cap.release()
        self.fps=c['source']['native_fps']
        if len(self.frames)!=c['source']['native_frames'] or abs((len(self.frames)-1)/self.fps-t['pickup'])>1e-6:
            raise ValueError('Native cache clock does not match pickup.')
        if not np.array_equal(self.frames[-1],self.last):
            raise ValueError('Pickup must be the exact final native cache frame.')
        self.track=np.array(c['subject'].get('anchor_track',[]),np.float32)
        if self.track.size:
            if self.track.ndim!=2 or self.track.shape[1]!=3 or np.any(np.diff(self.track[:,0])<=0):
                raise ValueError('anchor_track must contain increasing [seconds,x,y] keyframes.')
            if abs(self.track[-1,0]-t['pickup'])>1e-4 or np.linalg.norm(self.track[-1,1:]-self.anchor)>.1:
                raise ValueError('Last anchor keyframe must equal pickup time and anchor.')
        bbox=Image.fromarray(self.alpha).getbbox()
        if bbox is None:raise ValueError('Empty subject.')
        self.bbox=bbox
        left=self.anchor[0]-bbox[0]
        end_x=self.w+left+max(70*self.k,.12*self.w)
        self.end=np.array(c['subject'].get('end') or [end_x,self.anchor[1]-.64*self.h],np.float32)
        if self.mode=='drag':
            self.end[1]=self.anchor[1]+c['subject'].get('ground_slope',0)*(self.end[0]-self.anchor[0])
        self.path=c['subject'].get('path')
        if self.path is not None:
            self.path=np.array(self.path,np.float32)
            if self.path.shape!=(4,2) or np.linalg.norm(self.path[0]-self.anchor)>.01:
                raise ValueError('path needs four Bezier points starting at anchor.')
            if self.mode=='drag':raise ValueError('Use end/ground_slope for ground dragging, not an airborne Bezier path.')
        rod=c['rod']
        self.rope=float(rod.get('line_length') or max(50*self.k,self.anchor[1]-.06*self.h))
        if self.rope<=0:raise ValueError('line_length must be positive.')
        base=self.root/rod['base_asset'] if rod.get('base_asset') else SKILL/'assets/rig/rod-base.png'
        charm=self.root/rod['pendant_asset'] if rod.get('pendant_asset') else SKILL/f"assets/rig/{c['variant']}.png"
        self.rod=premul(rgba(base));self.charm=premul(rgba(charm))
        if self.rod.shape!=(1254,1254,4) or self.charm.shape!=(1254,1254,4):
            raise ValueError('Rig layers need the registered 1254x1254 canvas.')
        self.rx,self.ry=np.meshgrid(np.arange(1254,dtype=np.float32),np.arange(1254,dtype=np.float32))
        d=TIP-GRIP;self.axis=d/np.dot(d,d)
        self.input_paths=[self.root/c['media']['native'],self.root/c['media']['pickup'],
            self.root/c['matte']['output'],self.root/c['cleanup']['plate'],base,charm]
        self.make_background()
        self.stats={}

    def make_background(self):
        cfg=self.c['cleanup'];plate=np.array(Image.open(self.root/cfg['plate']).convert('RGB').resize((self.w,self.h),Image.Resampling.LANCZOS))
        shape=(self.h,self.w)
        if cfg.get('mask_file'):
            mask_path=self.root/cfg['mask_file'];patch=np.array(Image.open(mask_path).convert('L')).astype(np.float32)/255
            self.input_paths.append(mask_path)
        elif cfg.get('mode')=='full':patch=np.ones(shape,np.float32)
        elif cfg.get('mode')=='band':
            y0,y1,y2,y3=cfg['band'];yy=np.arange(self.h,dtype=np.float32)[:,None]
            a=np.clip((yy-y0)/(y1-y0),0,1);b=np.clip((y3-yy)/(y3-y2),0,1)
            patch=np.broadcast_to(a*a*(3-2*a)*b*b*(3-2*b),shape).copy()
        else:
            size=max(1,int(cfg.get('dilate',35)))|1
            patch=cv2.dilate(self.alpha,np.ones((size,size),np.uint8)).astype(np.float32)/255
            for e in cfg.get('ellipses',[]):
                cx,cy,rx,ry=e[:4]
                cv2.ellipse(patch,(int(cx),int(cy)),(int(rx),int(ry)),e[4] if len(e)>4 else 0,0,360,1,-1)
            sigma=cfg.get('feather',10)
            if sigma>0:patch=cv2.GaussianBlur(patch,(0,0),sigma)
            # Feather only the outer cleanup boundary; never restore the robot.
            mandatory=cv2.dilate((self.alpha>4).astype(np.float32),np.ones((5,5),np.uint8))
            for e in cfg.get('ellipses',[]):
                cx,cy,rx,ry=e[:4]
                cv2.ellipse(mandatory,(int(cx),int(cy)),(int(rx),int(ry)),e[4] if len(e)>4 else 0,0,360,1,-1)
            patch=np.maximum(patch,mandatory)
        if patch.shape!=shape or np.any((self.alpha>200)&(patch<.98)):
            raise ValueError('Cleanup must fully cover the original subject; expand the mask/band.')
        if cfg.get('protect_mask_file'):
            path=self.root/cfg['protect_mask_file'];protect=np.array(Image.open(path).convert('L')).astype(np.float32)/255
            if protect.shape!=shape:raise ValueError('Protection mask dimensions mismatch.')
            patch*=1-protect*(1-(self.alpha>4).astype(np.float32))
            self.input_paths.append(path)
        self.patch=patch
        self.clean=np.rint(self.last*(1-patch[...,None])+plate*patch[...,None]).astype(np.uint8)
        Image.fromarray(self.clean).save(self.root/'assets/background-composite.png')
        outside=1-cv2.dilate((self.alpha>4).astype(np.float32),np.ones((7,7),np.uint8))
        shadow=patch*outside
        if cfg.get('shadow_mask_file'):
            path=self.root/cfg['shadow_mask_file'];mask=np.array(Image.open(path).convert('L')).astype(np.float32)/255
            if mask.shape!=shape:raise ValueError('Shadow mask dimensions mismatch.')
            shadow*=mask;self.input_paths.append(path)
        self.shadow=(self.last.astype(np.float32)-self.clean)*shadow[...,None]
        if self.mode=='drag':self.shadow=np.minimum(self.shadow,0)
        self.shadow=cv2.GaussianBlur(self.shadow,(0,0),max(.3,2*self.k))

    def native(self,t):
        index=min(len(self.frames)-1,int(t*self.fps+1e-5))
        anchor=self.anchor.copy()
        if self.track.size:
            anchor=np.array([np.interp(t,self.track[:,0],self.track[:,i]) for i in (1,2)],np.float32)
        return self.frames[index],anchor

    def pose(self,t):
        u=float(np.clip((t-self.t['lift'])/(self.t['exit']-self.t['lift']),0,1))
        if self.mode=='drag':
            q=u**1.35;hook=self.anchor+(self.end-self.anchor)*q
        elif self.path is not None:
            p0,p1,p2,p3=self.path;hook=(1-u)**3*p0+3*(1-u)**2*u*p1+3*(1-u)*u*u*p2+u**3*p3
        else:
            hook=self.anchor+(self.end-self.anchor)*np.array([u*u,u**1.3],np.float32)
        angle=self.c['subject'].get('swing_degrees',3)*math.sin(math.pi*u)
        return matrix(self.anchor,hook,angle),hook,u

    def rig(self,t,hook,u):
        cfg=self.c['rod'];tt=self.t
        enter=smooth(tt['enter_start'],tt['enter_end'],t)
        load_start=max(tt['enter_end'],tt['contact']-.01)
        load_weight=smooth(load_start,tt['pickup']+.02,t)
        tau=max(0,t-tt['pickup'])
        angle=30+(cfg.get('angle',19)-30)*enter+3*load_weight*math.exp(-tau/.7)
        lean=math.radians((65*smooth(tt['pickup'],tt['exit'],t)) if self.mode=='drag' else 10*math.sin(math.pi*u))
        target=hook+np.array([self.rope*math.sin(lean),-self.rope*math.cos(lean)],np.float32)
        start=np.array([self.w+max(.32*self.w,90*self.k),max(.15*self.h,target[1]+.08*self.h)],np.float32)
        tip=start*(1-enter)+target*enter
        tip[1]-=27*self.k*math.sin(math.pi*enter)
        bend=np.array([0,self.k*(24*load_weight*(1-.6*smooth(0,.75,tau))+1.8*math.sin(tau*20)*math.exp(-tau*5))],np.float32)
        linear=rotation(angle)*cfg['scale'];grip=tip-bend-linear@(TIP-GRIP)
        rm=matrix(GRIP,grip,angle,cfg['scale'])
        local=np.linalg.inv(linear)@bend;sx=self.rx.copy();sy=self.ry.copy()
        for _ in range(3):
            q=np.clip((sx-GRIP[0])*self.axis[0]+(sy-GRIP[1])*self.axis[1],0,1)
            sx=self.rx-local[0]*q*q;sy=self.ry-local[1]*q*q
        bent=cv2.remap(self.rod,sx.astype(np.float32),sy.astype(np.float32),cv2.INTER_LINEAR)
        rod=cv2.warpAffine(bent,rm,(self.w,self.h),flags=cv2.INTER_LINEAR)
        cuff=rm[:,:2]@CUFF+rm[:,2]+bend*float(np.clip(np.dot(CUFF-GRIP,self.axis),0,1))**2
        sway=6*math.sin(2*math.pi*enter)*math.sin(math.pi*enter)+7*math.sin(tau*11)*math.exp(-tau*3)
        pendant_anchor=cfg.get('pendant_anchor') or {'follow':[702,680],'like':[702,680],'save':[657,690],'share':[661,708]}[self.c['variant']]
        cm=matrix(pendant_anchor,cuff,cfg.get('pendant_angle',-10)+sway,cfg.get('pendant_scale',cfg['scale']))
        charm=cv2.warpAffine(self.charm,cm,(self.w,self.h),flags=cv2.INTER_LINEAR)
        return rod,charm,tip,rm,load_weight

    def line(self,t,tip,hook,load_weight):
        tt=self.t;drop=smooth(tt['line_start'],tt['contact'],t)
        enter=smooth(tt['enter_start'],tt['enter_end'],t)
        start_length=min(110*self.k,self.rope*.55)
        end=tip+np.array([16*self.k*math.sin(math.pi*enter),start_length+(self.rope-start_length)*drop],np.float32)
        if t>=tt['contact']:end=hook
        bow=8*self.k*math.sin(math.pi*drop)*(1-load_weight)
        if t>=tt['contact']:bow=6*self.k*(1-load_weight)
        q=np.linspace(0,1,90)[:,None];mid=(tip+end)/2+np.array([bow,0])
        curve=(1-q)**2*tip+2*q*(1-q)*mid+q*q*end
        layer=np.zeros((self.h*2,self.w*2,4),np.uint8);pts=np.round(curve*2).astype(np.int32)
        cv2.polylines(layer,[pts],False,(43,48,46,200),max(1,round(3*self.k)),cv2.LINE_AA)
        cv2.polylines(layer,[pts+np.array([1,0])],False,(224,225,213,140),1,cv2.LINE_AA)
        ep=np.round(end*2).astype(int)
        cv2.ellipse(layer,(int(ep[0]+2),int(ep[1]-4)),(max(2,round(6*self.k)),max(2,round(8*self.k))),-10,10,265,(175,169,132,245),max(1,round(2*self.k)),cv2.LINE_AA)
        return premul(cv2.resize(layer,(self.w,self.h),interpolation=cv2.INTER_AREA)),end

    def render(self,t):
        bm,hook,u=self.pose(t);subject_count=0
        if t<=self.t['pickup']+1e-9:
            native,hook=self.native(t);bg=native.copy();base='original_video'
        else:
            if self.mode=='lift':
                height=max(0,float(self.anchor[1]-hook[1]));fade=math.exp(-height/max(1,.051*self.h))
                residual=self.shadow*fade
            else:
                residual=cv2.warpAffine(self.shadow,matrix(self.anchor,hook),(self.w,self.h),flags=cv2.INTER_LINEAR)
            bg=np.rint(np.clip(self.clean+residual,0,255)).astype(np.uint8)
            subject=np.zeros((self.h,self.w,4),np.float32)
            for dt,weight in [(-1/240,.25),(0,.5),(1/240,.25)]:
                m,_,_=self.pose(max(self.t['pickup'],t+dt))
                subject+=cv2.warpAffine(self.bot,m,(self.w,self.h),flags=cv2.INTER_LINEAR)*weight
            subject_count=int((subject[...,3]>.01).sum());bg=over(bg,subject);base='pickup_composite'
        rod,charm,tip,rm,load_weight=self.rig(t,hook,u)
        line,end=self.line(t,tip,hook,load_weight)
        bg=over(bg,line);bg=over(bg,rod)
        crank=np.zeros((self.h*2,self.w*2,4),np.uint8)
        a=rm[:,:2]@np.array([799.,982.])+rm[:,2];b=rm[:,:2]@np.array([826.,1012.])+rm[:,2]
        aa=tuple(np.round(a*2).astype(int));bb=tuple(np.round(b*2).astype(int))
        cv2.line(crank,aa,bb,(62,53,37,255),max(1,round(9*self.k)),cv2.LINE_AA)
        cv2.line(crank,aa,bb,(178,149,85,255),max(1,round(4*self.k)),cv2.LINE_AA)
        cv2.ellipse(crank,bb,(max(2,round(12*self.k)),max(2,round(18*self.k))),15,0,360,(29,31,29,255),-1,cv2.LINE_AA)
        crank=premul(cv2.resize(crank,(self.w,self.h),interpolation=cv2.INTER_AREA))
        bg=over(bg,crank);bg=over(bg,charm)
        self.stats={'base':base,'hook':hook.tolist(),'tip':tip.tolist(),'rope_length':float(np.linalg.norm(tip-end)),
            'subject_visible_pixels':subject_count,'rod_visible_pixels':int((rod[...,3]>.01).sum()),
            'pendant_visible_pixels':int((charm[...,3]>.01).sum()),'line_visible_pixels':int((line[...,3]>.01).sum()),
            'crank_visible_pixels':int((crank[...,3]>.01).sum())}
        return bg

    def exit_check(self):
        self.render(self.t['end']-1/60)
        return {k:v for k,v in self.stats.items() if k.endswith('_visible_pixels')}
