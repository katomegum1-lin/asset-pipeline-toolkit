# -*- coding: utf-8 -*-
"""
测试场景生成器 make_test_scene.py
=================================================
用途：生成一个「故意包含各类规范问题」的场景，用来验证 asset_lint.py 能否正确检错。
命令行运行：
    blender --background --python make_test_scene.py
=================================================
每个测试物体对应一种典型错误，检查器报告里应该都能看到。
"""
import bpy
import bmesh
import os
import sys

# 把自己的目录加进搜索路径，方便 import asset_lint
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# 清空场景
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene


def _new_obj(name, mesh):
    obj = bpy.data.objects.new(name, mesh)
    scene.collection.objects.link(obj)
    return obj


# ---------- 1. 规范样本：应该零错误 ----------
bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))
good = bpy.context.object
good.name = "SM_GoodCube"
good.data.name = "SM_GoodCube_Mesh"

# ---------- 2. 中文 + 空格命名 ----------
bpy.ops.mesh.primitive_cube_add(location=(3, 0, 0))
o = bpy.context.object
o.name = "立方体 01"

# ---------- 3. 缩放未应用 ----------
bpy.ops.mesh.primitive_cube_add(location=(6, 0, 0))
o = bpy.context.object
o.name = "SM_ScaledNotApplied"
o.scale = (2.0, 2.0, 2.0)

# ---------- 4. 负缩放（法线必反）----------
bpy.ops.mesh.primitive_cube_add(location=(9, 0, 0))
o = bpy.context.object
o.name = "SM_NegativeScale"
o.scale = (-1.0, 1.0, 1.0)

# ---------- 5. 无 UV ----------
bpy.ops.mesh.primitive_cube_add(location=(12, 0, 0))
o = bpy.context.object
o.name = "SM_NoUV"
for _l in list(o.data.uv_layers):
    o.data.uv_layers.remove(_l)

# ---------- 6. UV 重叠：把前两个面的 UV 设成完全一样 ----------
bpy.ops.mesh.primitive_cube_add(location=(15, 0, 0))
o = bpy.context.object
o.name = "SM_UVOverlap"
uv = o.data.uv_layers.active.data
p0, p1 = o.data.polygons[0], o.data.polygons[4]
# 面 0 的四个 UV 坐标复制给面 4
for k, li in enumerate(p1.loop_indices):
    src = p0.loop_indices[k % 4]
    uv[li].uv = uv[src].uv

# ---------- 7. 空材质槽 ----------
bpy.ops.mesh.primitive_cube_add(location=(18, 0, 0))
o = bpy.context.object
o.name = "SM_EmptyMatSlot"
try:
    o.data.materials.append(None)
except Exception:
    bpy.context.view_layer.objects.active = o
    bpy.ops.object.material_slot_add()

# ---------- 8. 材质槽过多（5 个）----------
bpy.ops.mesh.primitive_cube_add(location=(21, 0, 0))
o = bpy.context.object
o.name = "SM_TooManySlots"
for i in range(5):
    m = bpy.data.materials.new("MAT_Test_%d" % i)
    o.data.materials.append(m)

# ---------- 9. 非流形边 + 游离顶点 + 退化面 ----------
me = bpy.data.meshes.new("SM_BadTopology_Mesh")
bm = bmesh.new()
v1 = bm.verts.new((0, 0, 0))
v2 = bm.verts.new((1, 0, 0))
v3 = bm.verts.new((0, 1, 0))
v4 = bm.verts.new((0, -1, 0))
v5 = bm.verts.new((0, 0, 1))
bm.faces.new((v1, v2, v3))
bm.faces.new((v2, v1, v4))
bm.faces.new((v1, v2, v5))          # 三个面共享 v1-v2 → 非流形
bm.verts.new((5, 5, 5))             # 孤立顶点
# 退化面：三个共线点
d1 = bm.verts.new((10, 0, 0))
d2 = bm.verts.new((11, 0, 0))
d3 = bm.verts.new((12, 0, 0))
try:
    bm.faces.new((d1, d2, d3))
except Exception:
    pass
bm.to_mesh(me)
bm.free()
_bad = _new_obj("SM_BadTopology", me)
_bad.location = (24, 0, 0)

# ---------- 10. 面数超标 ----------
bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=6, location=(30, 0, 0))
o = bpy.context.object
o.name = "SM_TooManyTris"
# 加细分到超 5 万三角面
mod = o.modifiers.new("sub", "SUBSURF")
mod.levels = 1
mod.render_levels = 1
bpy.context.view_layer.objects.active = o
bpy.ops.object.modifier_apply(modifier=mod.name)

# ---------- 运行检查 ----------
import asset_lint
asset_lint.SCAN_MODE = "ALL"                      # 命令行下全部扫描
asset_lint.REPORT_DIR = os.path.join(HERE, "..", "..", "测试报告")
findings = asset_lint.run_lint()

print("\n[测试] 共发现 %d 条问题" % len(findings))
