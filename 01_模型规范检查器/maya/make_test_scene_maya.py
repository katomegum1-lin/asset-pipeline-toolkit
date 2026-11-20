# -*- coding: utf-8 -*-
"""
Maya 测试场景生成器
=================================================
用法：mayapy make_test_scene_maya.py
生成一批故意违规的模型，用来验证 asset_lint_maya.py 的检错能力。
=================================================
"""
import maya.standalone
maya.standalone.initialize(name="python")

import maya.cmds as cmds
import maya.api.OpenMaya as om2
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

cmds.file(new=True, force=True)


def _clean(name):
    """清理构造历史 —— 这是「能进引擎」的标准状态，规范样本必须做到"""
    cmds.delete(name, constructionHistory=True)
    return name


# ---------- 1. 规范样本：应该零问题 ----------
_clean(cmds.polyCube(name="SM_GoodCube")[0])

# ---------- 2. 缺少规范前缀 ----------
_clean(cmds.polyCube(name="BadName")[0])

# ---------- 3. 缩放未冻结 ----------
o = _clean(cmds.polyCube(name="SM_ScaledNotFrozen")[0])
cmds.setAttr(o + ".scale", 2, 2, 2)

# ---------- 4. 负缩放 ----------
o = _clean(cmds.polyCube(name="SM_NegativeScale")[0])
cmds.setAttr(o + ".scaleX", -1)

# ---------- 5. 无 UV（UV 集还在但 UV 数据被清空）----------
o = _clean(cmds.polyCube(name="SM_NoUV")[0])
cmds.polyMapDel(o + ".map[*]")

# ---------- 6. UV 重叠：两个平面重合并合并（UV 都占 0~1）----------
p1 = cmds.polyPlane(name="tmp1", w=1, h=1, sx=1, sy=1)[0]
p2 = cmds.polyPlane(name="tmp2", w=1, h=1, sx=1, sy=1)[0]
cmds.move(2, 0, 0, p2)
o = cmds.polyUnite(p1, p2, name="SM_UVOverlap", mergeUVSets=1, ch=False)[0]
_clean(o)

# ---------- 7. 非流形（API 构造：3 个面共享一条边）----------
pts = [(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1)]
poly_counts = [3, 3, 3]
connects = [0, 1, 2,   1, 0, 3,   0, 1, 4]
par = om2.MFnTransform()
par_mobj = par.create()
point_arr = om2.MPointArray([om2.MPoint(p) for p in pts])
count_arr = om2.MIntArray(poly_counts)
conn_arr = om2.MIntArray(connects)
mfn = om2.MFnMesh()
mfn.create(point_arr, count_arr, conn_arr, parent=par_mobj)
shape = mfn.fullPathName()
parents = cmds.listRelatives(shape, parent=True) or []
if parents:
    cmds.rename(parents[0], "SM_NonManifold")

# ---------- 8. Lamina 重合面 ----------
p1 = cmds.polyPlane(name="tmp3", w=1, h=1, sx=1, sy=1)[0]
p2 = cmds.polyPlane(name="tmp4", w=1, h=1, sx=1, sy=1)[0]
o = cmds.polyUnite(p1, p2, name="SM_Lamina", mergeUVSets=1, ch=False)[0]
_clean(o)

# ---------- 8b. Lamina 重合面（API 构造：polyUnite 会自动焊点，造不出来）----------
pts2 = [(0, 0, 0), (1, 0, 0), (0, 1, 0),
        (0, 0, 0), (1, 0, 0), (0, 1, 0)]
par2 = om2.MFnTransform()
par2_mobj = par2.create()
m2 = om2.MFnMesh()
m2.create(om2.MPointArray([om2.MPoint(p) for p in pts2]),
          om2.MIntArray([3, 3]),
          om2.MIntArray([0, 1, 2, 3, 4, 5]),
          parent=par2_mobj)
sh2 = m2.fullPathName()
pars2 = cmds.listRelatives(sh2, parent=True) or []
if pars2:
    cmds.rename(pars2[0], "SM_LaminaFaces")

# ---------- 9. 面数超标 ----------
_clean(cmds.polySphere(name="SM_TooManyTris", sx=180, sy=180)[0])

# ---------- 10. 构造历史未清理（故意不 clean）----------
cmds.polyCube(name="SM_WithHistory", subdivisionsWidth=2,
              subdivisionsHeight=2, subdivisionsDepth=2)

# ---------- 运行检查 ----------
import asset_lint_maya
asset_lint_maya.SCAN_MODE = "ALL"
asset_lint_maya.REPORT_DIR = os.path.join(HERE, "..", "..", "测试报告")
findings = asset_lint_maya.run_lint()

print("\n[测试] 共发现 %d 条问题" % len(findings))
cmds.quit(force=True)
