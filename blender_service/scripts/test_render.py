import bpy
import os
import mathutils
import math
import sys

full_input = "/app/media/stl_files/BoxLoc-AmmoBox_D-5_7x28-Combo_1_1.obj"
full_output = "/app/media/thumbnails/test_obj_thumbnail.png"

bpy.ops.wm.read_factory_settings(use_empty=True)

# Set render engine to CYCLES for headless CPU rendering without OpenGL
bpy.context.scene.render.engine = "CYCLES"
if hasattr(bpy.context.scene, 'cycles'):
    bpy.context.scene.cycles.device = "CPU"
    bpy.context.scene.cycles.samples = 16
    bpy.context.scene.cycles.use_denoising = True

try:
    bpy.ops.wm.obj_import(filepath=full_input)
except AttributeError:
    bpy.ops.import_scene.obj(filepath=full_input)

objs = bpy.context.selected_objects
if not objs:
    raise RuntimeError("Failed to import STL")
obj = objs[0]

# Calculate bounding box
bbox = [obj.matrix_world @ mathutils.Vector(b) for b in obj.bound_box]
min_x = min([v.x for v in bbox])
max_x = max([v.x for v in bbox])
min_y = min([v.y for v in bbox])
max_y = max([v.y for v in bbox])
min_z = min([v.z for v in bbox])
max_z = max([v.z for v in bbox])

dx = max_x - min_x
dy = max_y - min_y
dz = max_z - min_z
max_dim = max(dx, dy, dz)

# Set object origin to center and move to 0,0,0
bpy.context.view_layer.objects.active = obj
bpy.ops.object.origin_set(type="ORIGIN_GEOMETRY", center="BOUNDS")
obj.location = (0, 0, 0)
obj.rotation_euler = (math.radians(-45), 0, math.radians(45))

# Set up camera
cam_data = bpy.data.cameras.new("Camera")
cam_obj = bpy.data.objects.new("Camera", cam_data)
bpy.context.scene.collection.objects.link(cam_obj)
bpy.context.scene.camera = cam_obj

cam_data.type = "PERSP"
cam_data.lens = 50

# Position camera
distance = max_dim * 1.8
cam_data.clip_end = max(10000.0, distance * 10.0)
cam_obj.location = (0, -distance, 0)
cam_obj.rotation_euler = (math.radians(90), 0, 0)

# Set up lighting
# Use World ambient light for soft fill
if not bpy.data.worlds:
    bpy.data.worlds.new("World")
world = bpy.data.worlds[0]
world.use_nodes = True
bg_node = world.node_tree.nodes.get("Background")
if bg_node:
    bg_node.inputs[0].default_value = (1.0, 1.0, 1.0, 1.0) # White ambient
    bg_node.inputs[1].default_value = 2.0 # High strength for mostly ambient lighting

# Main light (very soft directional)
light_data = bpy.data.lights.new(name="Sun", type='SUN')
light_data.energy = 0.5
light_data.angle = math.radians(45) # Extremely soft shadows
light_obj = bpy.data.objects.new(name="Sun", object_data=light_data)
bpy.context.scene.collection.objects.link(light_obj)
light_obj.rotation_euler = (math.radians(60), 0, math.radians(45))

# Add material (Matte Gray)
mat = bpy.data.materials.new(name="PartMaterial")
mat.use_nodes = True
bsdf = mat.node_tree.nodes.get("Principled BSDF")
if bsdf:
    if "Base Color" in bsdf.inputs:
        bsdf.inputs["Base Color"].default_value = (0.85, 0.85, 0.85, 1.0) # Light gray/off-white
    if "Roughness" in bsdf.inputs:
        bsdf.inputs["Roughness"].default_value = 1.0 # Completely matte
    if "Metallic" in bsdf.inputs:
        bsdf.inputs["Metallic"].default_value = 0.0
    if "Specular" in bsdf.inputs:
        bsdf.inputs["Specular"].default_value = 0.0

if obj.data.materials:
    obj.data.materials[0] = mat
else:
    obj.data.materials.append(mat)

# Render settings
bpy.context.scene.render.film_transparent = True
bpy.context.scene.render.resolution_x = 512
bpy.context.scene.render.resolution_y = 512
bpy.context.scene.render.filepath = full_output

bpy.ops.render.render(write_still=True)
print(f"Rendered thumbnail to {full_output}. max_dim={max_dim}, distance={distance}, obj_loc={obj.location}, cam_loc={cam_obj.location}")
