"""Inference-only PyTorch transcription of the original TRUEFAD Keras graph.

Layer order, activations, skip concatenation and asymmetric SAME padding are
preserved. Weights are transposed from the original file, never retrained.
"""
import cv2
import numpy as np
import torch
from torch import nn
from torch.nn import functional as F


class TruefadModel(nn.Module):
    def __init__(self):
        super().__init__()
        shapes=[(1,64,3),(64,64,3),(64,128,3),(128,128,3),
                (128,256,3),(256,256,3),(256,128,2),(256,128,3),
                (128,128,3),(128,64,2),(128,64,3),(64,64,3),(64,2,3),(2,1,1)]
        self.convs=nn.ModuleList([nn.Conv2d(a,b,k,padding=1 if k==3 else 0) for a,b,k in shapes])

    def forward(self,x):
        a=F.relu(self.convs[1](F.relu(self.convs[0](x))))
        b=F.relu(self.convs[3](F.relu(self.convs[2](F.max_pool2d(a,2)))))
        x=F.relu(self.convs[5](F.relu(self.convs[4](F.max_pool2d(b,2)))))
        x=F.relu(self.convs[6](F.pad(F.interpolate(x,scale_factor=2,mode='nearest'),(0,1,0,1))))
        x=F.relu(self.convs[8](F.relu(self.convs[7](torch.cat((b,x),1)))))
        x=self.convs[9](F.pad(F.interpolate(x,scale_factor=2,mode='nearest'),(0,1,0,1)))
        x=self.convs[10](torch.cat((a,x),1))
        return torch.sigmoid(self.convs[13](self.convs[12](self.convs[11](x))))


def predict_centers(gray,weights,device='cpu'):
    gray=np.asarray(gray)
    if gray.ndim!=2 or not gray.size or not np.isfinite(gray).all():
        raise ValueError('TRUEFAD requires a finite, nonempty grayscale image.')
    model=TruefadModel()
    model.load_state_dict(torch.load(weights,map_location='cpu',weights_only=True))
    model.to(device).eval()
    h,w=gray.shape;side=min(h,w)
    # Historical overlapping-square preprocessing, extended to cover any aspect ratio.
    last=max(h,w)-side
    starts=list(range(0,last+1,max(1,side//2)))
    if starts[-1]!=last:starts.append(last)
    total=np.zeros((h,w),np.float32);count=np.zeros((h,w),np.float32)
    line=np.minimum(np.arange(side)+1,np.arange(side,0,-1)).astype(np.float32)
    window=np.minimum(line[None,:],line[:,None]);window/=window.max()
    with torch.inference_mode():
        for start in starts:
            x,y=(start,0) if w>=h else (0,start)
            tile=cv2.resize(gray[y:y+side,x:x+side],(512,512),interpolation=cv2.INTER_CUBIC)
            low,high=np.percentile(tile,[1,99.8])
            data=((tile.astype(np.float32)-low)/max(float(high-low),1e-20)).astype(np.float32)
            output=model(torch.from_numpy(data[None,None]).to(device)).cpu().numpy()[0,0]
            restored=cv2.resize(output,(side,side),interpolation=cv2.INTER_CUBIC)
            total[y:y+side,x:x+side]+=restored*window;count[y:y+side,x:x+side]+=window
    return total/count

