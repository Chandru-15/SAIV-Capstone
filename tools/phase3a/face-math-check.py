"""EAR geometry regression only. Synthetic landmarks are NOT liveness evidence."""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.getcwd())
from app.main import detect_blink, _LEFT_EYE_EAR_IDX, _RIGHT_EYE_EAR_IDX


def eyes(width, height, opening):
    points = [SimpleNamespace(x=0., y=0.) for _ in range(468)]
    for center, indices in [(100., _LEFT_EYE_EAR_IDX), (150., _RIGHT_EYE_EAR_IDX)]:
        pixels = [(0,0),(3,opening),(7,opening),(10,0),(7,-opening),(3,-opening)]
        for index, (x,y) in zip(indices,pixels):
            points[index] = SimpleNamespace(x=(center+x)/width,y=(100+y)/height)
    return SimpleNamespace(landmark=points)


for opening, expected, closed in [(1.5,.30,False),(.5,.10,True)]:
    for width,height in [(640,480),(480,640),(1280,720)]:
        actual_closed, ear = detect_blink(eyes(width,height,opening),(width,height))
        assert actual_closed is closed and abs(ear-expected)<1e-10
print('PASS EAR returns expected open/closed geometry across landscape, portrait and wide frames')
assert detect_blink(eyes(640,480,1.5),(0,480)) == (False,None)
points = eyes(640,480,1.5)
points.landmark[_LEFT_EYE_EAR_IDX[0]].x = float('nan')
assert detect_blink(points,(640,480)) == (False,None)
print('PASS unavailable/non-finite geometry is not classified as closed eyes')
print('Synthetic geometry tests only; live open-closed-open capture and anti-spoofing remain unverified')
