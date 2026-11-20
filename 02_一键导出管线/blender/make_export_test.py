# -*- coding: utf-8 -*-
"""
导出管线测试
=================================================
验证三件事：
  1. 非法命名能被自动修正（中文/空格/连字符）
  2. 未应用的缩放能被烘进网格，导出后尺寸正确
  3. 导出不修改原场景 —— 原物体名字和变换保持不变

用法：blender --background --python make_export_test.py
=================================================
"""
import bpy
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene

# ---- 造一个「脏」物体：中文+空格名、缩放未应用 ----
bpy.ops.mesh.primitive_cube_add(size=2.0, location=(0, 0, 0))
box = bpy.context.object
box.name = "箱子 01"
box.scale = (2.0, 2.0, 2.0)          # 世界尺寸应该是 4

print("\n[原场景] 名字=%r  缩放=%s  世界尺寸=%s" % (
    box.name, tuple(round(v, 3) for v in box.scale),
    tuple(round(v, 3) for v in box.dimensions)))

# ---- 导出 ----
import ue_export
ue_export.EXPORT_DIR = os.path.join(HERE, "..", "..", "测试报告", "_UEExport")
ue_export.PREFIX = "SM_"

bpy.ops.object.select_all(action="DESELECT")
box.select_set(True)
bpy.context.view_layer.objects.active = box

results = ue_export.export_selected()

# ---- 检查 1：原场景没被改 ----
print("[验证1] 原物体名字=%r（应仍是「箱子 01」）" % box.name)
print("[验证1] 原物体缩放=%s（应仍是 2,2,2）" %
      (tuple(round(v, 3) for v in box.scale),))
print("[验证1] 原物体世界尺寸=%s（应仍是 4,4,4）" %
      (tuple(round(v, 3) for v in box.dimensions),))
assert box.name == "箱子 01", "原物体被改名了！"
assert abs(box.scale[0] - 2.0) < 1e-4, "原物体缩放被改了！"

# ---- 检查 2：FBX 文件生成了，名字被修正 ----
fbs = [r for r in results if r[2]]
assert fbs, "没有任何文件导出成功"
out_name = fbs[0][1]
print("[验证2] 导出文件名 = %s" % out_name)
assert out_name.startswith("SM_"), "前缀没补上"
assert " " not in out_name, "文件名里还有空格"

# ---- 检查 3：重新导入，尺寸应该还是 4 ----
fpath = os.path.join(ue_export.EXPORT_DIR, out_name)
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.fbx(filepath=fpath)
imported = [o for o in bpy.context.scene.objects if o.type == "MESH"]
if imported:
    o = imported[0]
    dims = tuple(round(v, 3) for v in o.dimensions)
    print("[验证3] 导入回来的名字=%r 尺寸=%s" % (o.name, dims))
    assert abs(dims[0] - 4.0) < 0.05, "尺寸不对！期望 4，实际 %s" % (dims,)
    assert abs(o.scale[0] - 1.0) < 1e-4, "导入后缩放不是 1，说明变换没烘进去"
    print("[验证3] 缩放已应用 = %s" % (tuple(round(v, 3) for v in o.scale),))
else:
    print("[验证3] 导入后没有网格！")
    sys.exit(1)

print("\n[结论] 全部检查通过：命名修正 ✓ 变换烘焙 ✓ 尺寸正确 ✓ 原场景未改 ✓")
