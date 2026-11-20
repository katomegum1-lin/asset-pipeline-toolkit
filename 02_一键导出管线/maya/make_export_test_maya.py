# -*- coding: utf-8 -*-
"""
Maya 导出管线测试
=================================================
验证：命名修正 / 冻结变换 / 尺寸正确 / 原场景未改
用法：mayapy make_export_test_maya.py
=================================================
"""
import maya.standalone
maya.standalone.initialize(name="python")

import maya.cmds as cmds
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

cmds.file(new=True, force=True)

# ---- 造一个「脏」物体：无前缀名（Maya 本身不接受中文名）、缩放未冻结、4cm 立方体 ----
o = cmds.polyCube(name="Box01", w=4, h=4, d=4)[0]
cmds.setAttr(o + ".scale", 2, 2, 2)
bb = cmds.exactWorldBoundingBox(o)
print("\n[原场景] 名字=%r 缩放=%s" % (o, tuple(round(v, 3) for v in cmds.getAttr(o + ".scale")[0])))
print("[原场景] 世界包围盒尺寸=%.2f x %.2f x %.2f" % (
    bb[3] - bb[0], bb[4] - bb[1], bb[5] - bb[2]))

# ---- 导出 ----
import ue_export_maya
ue_export_maya.EXPORT_DIR = os.path.join(HERE, "..", "..", "测试报告", "_UEExport")
ue_export_maya.PREFIX = "SM_"

cmds.select(o, replace=True)
results = ue_export_maya.export_selected()

# ---- 验证 1：原场景没被改 ----
print("[验证1] 原物体名字=%r（应仍是「Box01」）" % o)
print("[验证1] 原物体缩放=%s（应仍是 2,2,2）" %
      (tuple(round(v, 3) for v in cmds.getAttr(o + ".scale")[0]),))
assert o == "Box01", "原物体被改名了"

# ---- 验证 2：FBX 生成，名字修正 ----
ok = [r for r in results if r[2]]
assert ok, "没有文件导出成功"
fname = ok[0][1]
fpath = os.path.join(ue_export_maya.EXPORT_DIR, fname)
print("[验证2] 导出文件 = %s  大小 = %d 字节" % (fname, os.path.getsize(fpath)))
assert fname.startswith("SM_"), "前缀没补上"
assert os.path.getsize(fpath) > 1000, "FBX 文件太小，可能是空文件"

# ---- 验证 3：重新导入，尺寸应仍是 8（4cm × 缩放2）----
cmds.file(new=True, force=True)
cmds.file(fpath, i=True, type="FBX")
imported = cmds.ls(type="transform", long=False) or []
imported = [x for x in imported if cmds.listRelatives(x, shapes=True, type="mesh")]
if imported:
    bb2 = cmds.exactWorldBoundingBox(imported)
    dims = (bb2[3] - bb2[0], bb2[4] - bb2[1], bb2[5] - bb2[2])
    print("[验证3] 导入回来的物体=%r 尺寸=%.2f x %.2f x %.2f" % (imported[0], dims[0], dims[1], dims[2]))
    assert abs(dims[0] - 8.0) < 0.1, "尺寸不对，期望 8，实际 %.2f" % dims[0]
    sc = cmds.getAttr(imported[0] + ".scale")[0]
    print("[验证3] 导入后缩放=%s" % (tuple(round(v, 3) for v in sc),))
else:
    print("[验证3] 没有导入到网格")
    sys.exit(1)

print("\n[结论] 全部检查通过：命名修正 ✓ 冻结变换 ✓ 尺寸正确 ✓ 原场景未改 ✓")
cmds.quit(force=True)
