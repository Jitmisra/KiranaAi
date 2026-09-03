"""A fake camera feed: a QR code held in the TOP-LEFT CORNER, rolled 37 degrees.

The corner and the roll are the point. A centre-cropping till reads nothing
here, so if the bill fills, the counter really is looking at the whole frame.
"""
import cv2, numpy as np, sys
W,H,FRAMES = 1280,720,90
e=cv2.QRCodeEncoder.create(); q=e.encode("gawaah:parle_g_biscuit")
q=(q*255).astype(np.uint8) if q.max()<=1 else q.astype(np.uint8)
QR=cv2.cvtColor(q,cv2.COLOR_GRAY2BGR)
t=cv2.resize(QR,(150,150),interpolation=cv2.INTER_NEAREST)
t=cv2.copyMakeBorder(t,12,12,12,12,cv2.BORDER_CONSTANT,value=(255,255,255))
M=cv2.getRotationMatrix2D((t.shape[1]/2,t.shape[0]/2),37,1.0)
t=cv2.warpAffine(t,M,(t.shape[1],t.shape[0]),borderMode=cv2.BORDER_REPLICATE)
out=open(sys.argv[1],'wb')
out.write(b"YUV4MPEG2 W%d H%d F25:1 Ip A1:1 C420\n"%(W,H))
for i in range(FRAMES):
    f=np.full((H,W,3),178,np.uint8)
    cv2.rectangle(f,(0,455),(W,H),(122,96,80),-1)
    y,x=42+(i%3),58+(i%3)                       # a hand that is not quite still
    f[y:y+t.shape[0], x:x+t.shape[1]]=t
    f=cv2.GaussianBlur(f,(3,3),0)
    yuv=cv2.cvtColor(f,cv2.COLOR_BGR2YUV_I420)
    out.write(b"FRAME\n"); out.write(yuv.tobytes())
out.close()
print("wrote", sys.argv[1])
