from django.shortcuts import render
import os
import zipfile
import json
import logging
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
from django.urls import reverse_lazy, reverse
from django.http import JsonResponse, HttpResponse, HttpResponseForbidden
from django.views.decorators.csrf import csrf_exempt
import re
from .models import Project, Part, Group, PurchasedPart, ProjectImage, Designer, Material, Instructions, UserSettings, Machine, UnclaimedSlice, Tag
from .forms import (
    ProjectForm, PartForm, GroupForm, PurchasedPartForm,
    ProjectImageForm, BulkUploadForm, DesignerForm, MaterialForm,
    MachineForm
)
from forge.services.ofd_client import OFDClient
from .color_utils import get_closest_color_name
from forge.module_registry import registry
from django.db.models import Count, Sum, Max, Q
from django.contrib.auth.decorators import login_required
import tempfile
from django.core.files import File
import numpy as np
from stl import mesh
from django.contrib.auth.models import User
from django.views.decorators.http import require_POST
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from django.contrib.auth import login
from django.contrib.auth.forms import UserCreationForm
from decimal import Decimal
from datetime import datetime

# Setup logger
logger = logging.getLogger(__name__)

# Create your views here.

class ProjectListView(ListView):
    model = Project
    template_name = 'projects/project_list.html'
    context_object_name = 'projects'

class ProjectDetailView(DetailView):
    model = Project
    template_name = 'projects/project_detail.html'
    context_object_name = 'project'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        project = self.object
        context['parts'] = project.parts.all().order_by('group__name', 'name')
        context['purchased_parts'] = project.purchased_parts.all()
        context['images'] = project.images.all()
        context['groups'] = project.groups.all()
        
        # Calculate material weights and costs
        material_info = {}
        for part in project.parts.all():
            if part.material:
                material = part.material
                if material.id not in material_info:
                    material_info[material.id] = {
                        'name': material.name,
                        'color': material.color,
                        'total_weight': Decimal('0'),
                        'total_cost': Decimal('0'),
                        'part_count': 0
                    }
                
                # Calculate weight for this part
                if part.volume and material.density:
                    # Convert volume from mm³ to cm³ (divide by 1000)
                    volume_cm3 = Decimal(str(part.volume)) / Decimal('1000')
                    # Calculate weight in grams: volume (cm³) × density (g/cm³)
                    weight_g = volume_cm3 * material.density
                    # Convert to kg and multiply by quantity
                    weight_kg = (weight_g / Decimal('1000')) * part.quantity
                    
                    material_info[material.id]['total_weight'] += weight_kg
                    material_info[material.id]['total_cost'] += weight_kg * (material.cost_per_kg or Decimal('0'))
                material_info[material.id]['part_count'] += part.quantity
        
        # Convert to list and sort by material name
        context['material_info'] = sorted(material_info.values(), key=lambda x: x['name'])
        
        # Include material colors in the context
        materials = Material.objects.filter(is_active=True).order_by('name')
        context['materials'] = materials
        context['material_colors'] = {str(m.id): m.color for m in materials if m.color}
        
        # Include instructions in the context
        context['instructions'] = project.instructions.all().order_by('order')
        
        # Include Forge modules
        context['forge_modules'] = [m for m in registry.get_all_modules() if m.get('enabled', True)]
        
        # Include PurchasedPart model for status choices
        context['PurchasedPart'] = PurchasedPart
        return context

class ProjectCreateView(CreateView):
    model = Project
    form_class = ProjectForm
    template_name = 'projects/project_form.html'
    success_url = reverse_lazy('projects:project_list')

    def form_valid(self, form):
        form.instance.user = self.request.user
        self.object = form.save()
        
        # Handle multiple file uploads
        files = self.request.FILES
        for key in files:
            if key.startswith('image_'):
                ProjectImage.objects.create(
                    project=self.object,
                    image=files[key]
                )
        
        if self.request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({
                'status': 'success',
                'redirect_url': reverse('projects:add_multiple_parts', args=[self.object.id])
            })
        else:
            messages.success(self.request, 'Project created successfully!')
            return redirect('projects:add_multiple_parts', self.object.id)

    def form_invalid(self, form):
        if self.request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({
                'status': 'error',
                'message': 'Please correct the errors below.',
                'errors': form.errors
            }, status=400)
        return super().form_invalid(form)

class ProjectUpdateView(UpdateView):
    model = Project
    form_class = ProjectForm
    template_name = 'projects/project_form.html'
    
    def get_success_url(self):
        return reverse_lazy('projects:project_detail', kwargs={'pk': self.object.pk})

    def form_valid(self, form):
        self.object = form.save()
        
        # Handle multiple file uploads
        files = self.request.FILES
        for key in files:
            if key.startswith('image_'):
                ProjectImage.objects.create(
                    project=self.object,
                    image=files[key]
                )
        
        if self.request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({
                'status': 'success',
                'redirect_url': self.get_success_url()
            })
        else:
            messages.success(self.request, 'Project updated successfully!')
            return redirect(self.get_success_url())

    def form_invalid(self, form):
        if self.request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({
                'status': 'error',
                'message': 'Please correct the errors below.',
                'errors': form.errors
            }, status=400)
        return super().form_invalid(form)

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        form = self.get_form()
        if form.is_valid():
            return self.form_valid(form)
        return self.form_invalid(form)

class ProjectDeleteView(DeleteView):
    model = Project
    success_url = reverse_lazy('projects:project_list')
    template_name = 'projects/project_confirm_delete.html'

    def dispatch(self, request, *args, **kwargs):
        project = self.get_object()
        is_test_project = 'test' in project.name.lower()

        # Allow deletion if it's a test project or the user is the owner
        if not is_test_project and project.user != request.user:
            return HttpResponseForbidden("You don't have authorization to view this page.")
        
        return super().dispatch(request, *args, **kwargs)

    def delete(self, request, *args, **kwargs):
        self.object = self.get_object()
        success_url = self.get_success_url()
        self.object.delete()
        messages.success(request, 'Project deleted successfully.')
        return redirect(success_url)

def upload_files(request, project_id):
    project = get_object_or_404(Project, id=project_id)
    
    if request.method == 'POST':
        form = BulkUploadForm(request.POST, request.FILES)
        if form.is_valid():
            zip_file = request.FILES['zip_file']
            
            with zipfile.ZipFile(zip_file) as z:
                for filename in z.namelist():
                    if filename.lower().endswith('.stl'):
                        # Extract the file
                        file_data = z.read(filename)
                        part_name = os.path.splitext(os.path.basename(filename))[0]
                        
                        # Create the part
                        part = Part.objects.create(
                            project=project,
                            name=part_name,
                            quantity=1,  # Default value
                            material='',  # Empty by default
                            color='',  # Empty by default
                            stl_file=file_data
                        )
            
            messages.success(request, 'Files uploaded successfully')
            return redirect('projects:project_detail', pk=project.id)
    else:
        form = BulkUploadForm()
    
    return render(request, 'projects/upload_files.html', {
        'form': form,
        'project': project
    })

def add_part(request, project_id):
    project = get_object_or_404(Project, id=project_id)
    
    if request.method == 'POST':
        form = PartForm(request.POST, request.FILES, project=project)
        if form.is_valid():
            part = form.save(commit=False)
            part.project = project
            part.save()
            
            # 3MF Explosion Logic
            if part.stl_file and part.stl_file.name.lower().endswith('.3mf'):
                try:
                    import trimesh
                    import tempfile
                    from django.core.files import File
                    
                    scene = trimesh.load(part.stl_file.path, force='scene')
                    
                    if isinstance(scene, trimesh.Scene) and hasattr(scene, 'graph'):
                        count = 1
                        for node_name in scene.graph.nodes_geometry:
                            geometry_id = scene.graph[node_name][1]
                            mesh = scene.geometry[geometry_id]
                            
                            child_filename = f"{part.name}_{count}.obj"
                            
                            with tempfile.NamedTemporaryFile(suffix='.obj', delete=False) as temp_obj:
                                mesh.export(temp_obj.name)
                                
                                with open(temp_obj.name, 'rb') as f:
                                    child_part = Part.objects.create(
                                        project=project,
                                        name=f"{part.name} - Part {count}",
                                        quantity=part.quantity,
                                        material=part.material,
                                        color=part.color,
                                        group=part.group,
                                        parent=part
                                    )
                                    child_part.stl_file.save(child_filename, File(f), save=True)
                                    
                            os.unlink(temp_obj.name)
                            count += 1
                except Exception as e:
                    import logging
                    logger = logging.getLogger(__name__)
                    logger.error(f"Error extracting 3MF parts for {part.name}: {e}")
            
            return redirect('projects:project_detail', pk=project.id)
    else:
        form = PartForm(project=project)
    
    return render(request, 'projects/part_form.html', {
        'form': form,
        'project': project
    })

@login_required
def add_group(request, project_id):
    project = get_object_or_404(Project, id=project_id)
    
    if request.method == 'POST':
        try:
            # Get the group name from form data
            name = request.POST.get('name')
            
            if not name:
                return JsonResponse({
                    'status': 'error',
                    'message': 'Group name is required'
                }, status=400)
                
            # Check if group with same name already exists
            if Group.objects.filter(project=project, name=name).exists():
                return JsonResponse({
                    'status': 'error',
                    'message': 'A group with this name already exists'
                }, status=400)
                
            # Create the group
            group = Group.objects.create(
                project=project,
                name=name
            )
            
            return JsonResponse({
                'status': 'success',
                'group_id': group.id,
                'group_name': group.name
            })
            
        except Exception as e:
            return JsonResponse({
                'status': 'error',
                'message': str(e)
            }, status=500)
            
    return JsonResponse({
        'status': 'error',
        'message': 'Invalid request method'
    }, status=405)

def add_purchased_part(request, project_id):
    project = get_object_or_404(Project, id=project_id)
    
    if request.method == 'POST':
        form = PurchasedPartForm(request.POST)
        if form.is_valid():
            part = form.save(commit=False)
            part.project = project
            part.save()
            return redirect('projects:project_detail', pk=project.id)
    else:
        form = PurchasedPartForm()
    
    return render(request, 'projects/purchased_part_form.html', {
        'form': form,
        'project': project
    })

def upload_project_images(request, project_id):
    project = get_object_or_404(Project, id=project_id)
    
    if request.method == 'POST':
        try:
            # Get all files from the request
            files = request.FILES
            success_count = 0
            
            # Process each uploaded file
            for key in files:
                if key.startswith('image_'):
                    # Create a new ProjectImage for each file
                    image = ProjectImage(
                        project=project,
                        image=files[key]
                    )
                    image.save()
                    success_count += 1
            
            if success_count > 0:
                messages.success(request, f'Successfully uploaded {success_count} image(s)')
            else:
                messages.warning(request, 'No images were uploaded')
                
            return redirect('projects:project_detail', pk=project.id)
            
        except Exception as e:
            messages.error(request, f'Error uploading images: {str(e)}')
            return redirect('projects:project_detail', pk=project.id)
    
    return render(request, 'projects/upload_images.html', {
        'project': project
    })

def update_part_completion(request, part_id):
    if request.method == 'POST':
        part = get_object_or_404(Part, id=part_id)
        completed = request.POST.get('completed', 0)
        try:
            completed = int(completed)
            if 0 <= completed <= part.quantity:
                part.completed = completed
                part.save()
                return JsonResponse({'status': 'success'})
        except ValueError:
            pass
    return JsonResponse({'status': 'error'}, status=400)

def update_part_group(request, part_id):
    if request.method == 'POST':
        part = get_object_or_404(Part, id=part_id)
        group_id = request.POST.get('group_id')
        
        if group_id:
            group = get_object_or_404(Group, id=group_id)
            part.group = group
        else:
            part.group = None
            
        part.save()
        return JsonResponse({'status': 'success'})
    return JsonResponse({'status': 'error'}, status=400)

def part_details(request, part_id):
    part = get_object_or_404(Part, id=part_id)
    unit_system = 'imperial'
    if request.user.is_authenticated:
        try:
            general_settings = request.user.usersettings_set.get(settings_type='general')
            settings_data = json.loads(general_settings.settings_data)
            unit_system = settings_data.get('unit_system', 'imperial')
        except Exception:
            pass

    if part.volume:
        if unit_system == 'metric':
            # Convert mm³ to cm³
            volume_val = float(part.volume) / 1000.0
        else:
            # Convert mm³ to in³
            volume_val = float(part.volume) / 16387.064
    else:
        volume_val = None

    data = {
        'name': part.name,
        'quantity': part.quantity,
        'material_id': part.material.id if part.material else None,
        'color': part.color,
        'group_id': part.group.id if part.group else None,
        'completed': part.completed,
        'volume': volume_val,
        'unit_system': unit_system,
        'material_cost': float(part.material_cost) if part.material_cost else None,
    }
    
    # Only include stl_url if the file exists
    if part.stl_file:
        data['stl_url'] = part.stl_file.url
    
    return JsonResponse(data)

def update_part(request, part_id):
    if request.method == 'POST':
        try:
            part = Part.objects.get(id=part_id)
            material_id = request.POST.get('material_id')
            
            # Update part fields
            part.name = request.POST.get('name', part.name)
            part.quantity = int(request.POST.get('quantity', part.quantity))
            part.color = request.POST.get('color', part.color)
            
            # Handle material assignment
            if material_id:
                try:
                    material = Material.objects.get(id=material_id)
                    part.material = material
                except Material.DoesNotExist:
                    return JsonResponse({'status': 'error', 'message': 'Material not found'}, status=400)
            else:
                part.material = None
            
            # Handle group assignment
            group_id = request.POST.get('group')
            if group_id:
                try:
                    group = Group.objects.get(id=group_id)
                    part.group = group
                except Group.DoesNotExist:
                    return JsonResponse({'status': 'error', 'message': 'Group not found'}, status=400)
            else:
                part.group = None
                
            part.save()
            return JsonResponse({'status': 'success'})
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=400)
    
    return JsonResponse({'status': 'error', 'message': 'Invalid request method'}, status=400)

@login_required
def update_purchased_part_status(request, part_id):
    if request.method == 'POST':
        try:
            part = PurchasedPart.objects.get(id=part_id)
            new_status = request.POST.get('status')
            
            # Validate status
            if new_status not in dict(PurchasedPart.STATUS_CHOICES):
                return JsonResponse({
                    'status': 'error',
                    'message': 'Invalid status'
                }, status=400)
                
            part.status = new_status
            part.save()
            
            return JsonResponse({
                'status': 'success',
                'message': 'Status updated successfully'
            })
        except PurchasedPart.DoesNotExist:
            return JsonResponse({
                'status': 'error',
                'message': 'Part not found'
            }, status=404)
        except Exception as e:
            return JsonResponse({
                'status': 'error',
                'message': str(e)
            }, status=500)
    return JsonResponse({
        'status': 'error',
        'message': 'Invalid request method'
    }, status=400)

def add_multiple_parts(request, project_id):
    project = get_object_or_404(Project, id=project_id)
    
    if request.method == 'POST':
        try:
            import time
            # Get the parts data from the request
            parts_data = json.loads(request.POST.get('parts', '[]'))
            created_parts = []
            
            # Process each part
            for part_data in parts_data:
                # Create the part
                part = Part.objects.create(
                    project=project,
                    name=part_data['name'],
                    quantity=part_data['quantity'],
                    material_id=part_data['material'],
                    color=part_data['color']
                )
                created_parts.append(part)
                
                # Set the group if specified
                if part_data.get('group'):
                    group = get_object_or_404(Group, id=part_data['group'])
                    part.group = group
                    part.save()
            
            # Handle file uploads — thumbnails + volumes are deferred to
            # a background pipeline with progress tracking
            files = request.FILES.getlist('files')
            processing_start_time = time.time()
            processed_files = 0
            pipeline_tasks = []  # (part_id, file_path) for background processing
            
            for i, file in enumerate(files):
                if i < len(created_parts):
                    part = created_parts[i]
                    # Skip synchronous volume + thumbnail — background pipeline handles both
                    part._skip_volume = True
                    part._skip_thumbnail = True
                    file_start_time = time.time()
                    part.stl_file.save(file.name, file, save=True)
                    pipeline_tasks.append((part.id, part.stl_file.path))
                    
                    file_process_time = time.time() - file_start_time
                    processed_files += 1
                    
                    logger.debug(f"Processed {part.name} in {file_process_time:.2f}s (thumbnails + volumes deferred)")
            
            total_processing_time = time.time() - processing_start_time
            
            # Create progress task and dispatch background pipeline
            task_id = None
            if pipeline_tasks:
                from projects.volume import process_parts_background, generate_task_id
                from projects import progress
                
                task_id = generate_task_id()
                progress.create_task(task_id, len(pipeline_tasks))
                # Mark upload phase as complete (files are saved)
                progress.update_task(task_id, phase='upload', completed=len(pipeline_tasks),
                                     status='Files saved')
                process_parts_background(task_id, pipeline_tasks)
            
            return JsonResponse({
                'status': 'success',
                'task_id': task_id,
                'total_parts': len(pipeline_tasks),
                'processing_time': total_processing_time,
                'files_processed': processed_files
            })
            
        except Exception as e:
            logger.error(f"Error in add_multiple_parts: {str(e)}")
            return JsonResponse({'status': 'error', 'message': str(e)}, status=400)
    
    # For GET requests, render the template
    groups = project.groups.all()
    materials = Material.objects.filter(is_active=True).order_by('name')
    material_types = Material.MATERIAL_TYPES
    return render(request, 'projects/add_parts.html', {
        'project': project,
        'groups': groups,
        'materials': materials,
        'material_types': material_types
    })


@login_required
def upload_progress(request, task_id):
    """Polling endpoint for upload progress. Called by frontend every ~500ms."""
    from projects import progress
    
    task = progress.get_task(task_id)
    if task is None:
        return JsonResponse({'status': 'not_found', 'done': True, 'percent': 100})
    
    return JsonResponse(task)



def copy_mirror_part(request, part_id):
    try:
        # First get the part without user check
        original_part = get_object_or_404(Part, id=part_id)
        project_id = original_part.project.id
        
        # Create a new part with "(mirrored)" appended to the name
        new_part = Part.objects.create(
            project=original_part.project,
            name=f"{original_part.name} (Left)",
            color=original_part.color,
            material=original_part.material,
            quantity=original_part.quantity,
            group=original_part.group,
            completed=0,
            stl_file=original_part.stl_file
        )
        original_part.name = f"{original_part.name} (Right)"
        original_part.save()
        
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({
                'status': 'success',
                'message': f'Successfully created mirrored part: {new_part.name}'
            })
        else:
            messages.success(request, f'Successfully created mirrored part: {new_part.name}')
            return redirect('projects:project_detail', pk=project_id)

    except Exception as e:
        logger.error(f'Error creating mirrored part: {str(e)}')
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({
                'status': 'error',
                'message': f'Error creating mirrored part: {str(e)}'
            }, status=500)
        else:
            messages.error(request, f'Error creating mirrored part: {str(e)}')
            # Try to get the project_id from the URL or session if available
            try:
                project_id = request.GET.get('project_id') or request.session.get('last_project_id')
                if project_id:
                    return redirect('projects:project_detail', pk=project_id)
            except:
                pass
            return redirect('projects:index')

@login_required
def project_create(request):
    if request.method == 'POST':
        form = ProjectForm(request.POST)
        if form.is_valid():
            project = form.save(commit=False)
            project.user = request.user
            project.save()
            
            # Handle multiple file uploads
            files = request.FILES
            for key in files:
                if key.startswith('image_'):
                    ProjectImage.objects.create(
                        project=project,
                        image=files[key]
                    )
            
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'status': 'success', 'redirect_url': reverse('projects:project_detail', args=[project.id])})
            else:
                messages.success(request, 'Project created successfully!')
                return redirect('projects:project_detail', project.id)
    else:
        form = ProjectForm()
    return render(request, 'projects/project_form.html', {'form': form})

@login_required
def designer_index(request):
    """Display a list of all designers and their projects."""
    # Get all users who have designed projects
    designers = Designer.objects.all()
    designer_data = []
    
    for designer in designers:
        # Get projects where this user is the designer
        projects = Project.objects.filter(designer=designer)
        total_projects = projects.count()
        total_parts = Part.objects.filter(project__in=projects).count()
        
        designer_data.append({
            'designer': designer,
            'total_projects': total_projects,
            'total_parts': total_parts,
            'projects': projects
        })
    
    # Handle form submission
    if request.method == 'POST':
        form = DesignerForm(request.POST, request.FILES)
        if form.is_valid():
            form.save()
            messages.success(request, 'Designer added successfully!')
            return redirect('projects:designer_index')
    else:
        form = DesignerForm()
    
    return render(request, 'projects/designer_index.html', {
        'designer_data': designer_data,
        'form': form
    })

@login_required
def designer_detail(request, designer_id):
    """Display detailed information about a designer and their projects."""
    designer = get_object_or_404(Designer, id=designer_id)
    projects = Project.objects.filter(designer=designer).order_by('-created_at')
    
    return render(request, 'projects/designer_detail.html', {
        'designer': designer,
        'projects': projects
    })

@login_required
def designer_update(request, designer_id):
    designer = get_object_or_404(Designer, id=designer_id)
    
    if request.method == 'POST':
        form = DesignerForm(request.POST, request.FILES, instance=designer)
        if form.is_valid():
            form.save()
            messages.success(request, 'Designer updated successfully!')
            return redirect('projects:designer_detail', designer_id=designer.id)
    else:
        form = DesignerForm(instance=designer)
    
    return render(request, 'projects/designer_form.html', {
        'form': form,
        'designer': designer
    })

@login_required
def material_list(request):
    """View for listing all materials."""
    materials = Material.objects.filter(is_active=True).order_by('name')
    return render(request, 'projects/material_list.html', {
        'materials': materials,
    })

@login_required
def material_create(request):
    """Create a new material."""
    if request.method == 'POST':
        form = MaterialForm(request.POST)
        if form.is_valid():
            material = form.save()
            messages.success(request, f'Material "{material.name}" created successfully.')
            return redirect('projects:material_list')
    else:
        form = MaterialForm()
    
    return render(request, 'projects/material_form.html', {
        'form': form,
        'title': 'Add Material',
    })

@login_required
@require_POST
def api_material_create(request):
    """Create a new material via AJAX."""
    try:
        # Extract data from POST
        name = request.POST.get('name')
        brand = request.POST.get('brand', '')
        type_val = request.POST.get('type', 'PLA')
        color = request.POST.get('color', '#000000')
        
        if not name:
            return JsonResponse({'status': 'error', 'message': 'Material name is required.'}, status=400)
            
        # Check if material with same name already exists
        if Material.objects.filter(name=name).exists():
            return JsonResponse({'status': 'error', 'message': 'A material with this name already exists.'}, status=400)
            
        material = Material.objects.create(
            name=name,
            brand=brand,
            type=type_val,
            color=color,
            is_active=True
        )
        
        return JsonResponse({
            'status': 'success',
            'material': {
                'id': material.id,
                'name': material.name,
                'brand': material.brand,
                'type_display': material.get_type_display(),
                'color': material.color
            }
        })
    except Exception as e:
        logger.error(f"Error creating material: {str(e)}")
        return JsonResponse({'status': 'error', 'message': str(e)}, status=400)


@login_required
def material_edit(request, material_id):
    """Edit an existing material."""
    material = get_object_or_404(Material, id=material_id)
    
    if request.method == 'POST':
        form = MaterialForm(request.POST, instance=material)
        if form.is_valid():
            material = form.save()
            messages.success(request, f'Material "{material.name}" updated successfully.')
            return redirect('projects:material_list')
    else:
        form = MaterialForm(instance=material)
    
    return render(request, 'projects/material_form.html', {
        'form': form,
        'title': f'Edit {material.name}',
        'material': material,
    })

@login_required
def download_stl(request, part_id):
    """Download the STL file for a part."""
    part = get_object_or_404(Part, id=part_id)
    
    if not part.stl_file:
        return JsonResponse({
            'status': 'error',
            'message': 'No STL file available for this part'
        }, status=404)
    
    response = HttpResponse(part.stl_file, content_type='application/sla')
    response['Content-Disposition'] = f'attachment; filename="{part.name}.stl"'
    return response

@login_required
def update_part_progress(request, part_id):
    if request.method == 'POST':
        try:
            part = Part.objects.get(id=part_id)
            completed = int(request.POST.get('completed', 0))
            
            # Validate completed value
            if completed < 0:
                completed = 0
            if completed > part.quantity:
                completed = part.quantity
                
            part.completed = completed
            part.save()
            
            # Calculate total completed parts for the project
            total_completed = Part.objects.filter(project=part.project).aggregate(
                total=Sum('completed')
            )['total'] or 0
            
            return JsonResponse({
                'status': 'success',
                'total_completed': total_completed
            })
        except Part.DoesNotExist:
            return JsonResponse({
                'status': 'error',
                'message': 'Part not found'
            }, status=404)
        except Exception as e:
            return JsonResponse({
                'status': 'error',
                'message': str(e)
            }, status=500)
    return JsonResponse({
        'status': 'error',
        'message': 'Invalid request method'
    }, status=400)

@login_required
def upload_instructions(request, project_id):
    project = get_object_or_404(Project, id=project_id)
    
    if request.method == 'POST':
        # Accept both 'files' and 'images' for backward compatibility
        files = request.FILES.getlist('files') or request.FILES.getlist('images')
        description_format = request.POST.get('description_format', 'filename')
        
        if not files:
            return JsonResponse({
                'status': 'error',
                'message': 'No files were uploaded'
            })
        
        # Validate file types
        allowed_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.pdf', '.doc', '.docx']
        invalid_files = []
        for file in files:
            ext = os.path.splitext(file.name.lower())[1]
            if ext not in allowed_extensions:
                invalid_files.append(file.name)
        
        if invalid_files:
            return JsonResponse({
                'status': 'error',
                'message': f'Invalid file types: {", ".join(invalid_files)}. Allowed types: images (jpg, png, gif, etc.), PDF, DOC, DOCX'
            })
        
        try:
            # Get the current highest order number
            current_highest_order = Instructions.objects.filter(project=project).aggregate(Max('order'))['order__max'] or 0
            
            # Sort files by filename
            files = sorted(files, key=lambda x: x.name)
            
            for index, file in enumerate(files, current_highest_order + 1):
                description = ''
                if description_format == 'filename':
                    description = os.path.splitext(file.name)[0]
                elif description_format == 'step':
                    description = f'Step {index}'
                
                Instructions.objects.create(
                    project=project,
                    file=file,
                    description=description,
                    order=index
                )
            
            return JsonResponse({
                'status': 'success',
                'message': f'Successfully uploaded {len(files)} instruction files'
            })
            
        except Exception as e:
            return JsonResponse({
                'status': 'error',
                'message': str(e)
            })
    
    # Get existing instructions ordered by their order field
    existing_instructions = Instructions.objects.filter(project=project).order_by('order')
    
    return render(request, 'projects/upload_instructions.html', {
        'project': project,
        'existing_instructions': existing_instructions
    })

@login_required
def delete_instruction(request, project_id):
    if request.method == 'POST':
        instruction_id = request.POST.get('instruction_id')
        try:
            instruction = Instructions.objects.get(id=instruction_id, project_id=project_id)
            instruction.delete()
            return JsonResponse({'status': 'success'})
        except Instructions.DoesNotExist:
            return JsonResponse({
                'status': 'error',
                'message': 'Instruction not found'
            })
        except Exception as e:
            return JsonResponse({
                'status': 'error',
                'message': str(e)
            })
    return JsonResponse({
        'status': 'error',
        'message': 'Invalid request method'
    })

@login_required
def purchased_part_details(request, part_id):
    try:
        part = PurchasedPart.objects.get(id=part_id)
        return JsonResponse({
            'name': part.name,
            'price': float(part.price),
            'quantity': part.quantity,
            'link': part.link,
            'status': part.status
        })
    except PurchasedPart.DoesNotExist:
        return JsonResponse({
            'status': 'error',
            'message': 'Purchased part not found'
        }, status=404)

@login_required
def update_purchased_part(request, part_id):
    try:
        part = PurchasedPart.objects.get(id=part_id)
        
        # Update fields
        part.name = request.POST.get('name', part.name)
        part.price = float(request.POST.get('price', part.price))
        part.quantity = int(request.POST.get('quantity', part.quantity))
        part.link = request.POST.get('link', part.link)
        part.status = request.POST.get('status', part.status)
        
        # Validate data
        if not part.name:
            return JsonResponse({
                'status': 'error',
                'message': 'Name is required'
            })
        if part.price < 0:
            return JsonResponse({
                'status': 'error',
                'message': 'Price must be a positive number'
            })
        if part.quantity < 1:
            return JsonResponse({
                'status': 'error',
                'message': 'Quantity must be at least 1'
            })
        
        part.save()
        
        return JsonResponse({
            'status': 'success',
            'message': 'Purchased part updated successfully'
        })
    except PurchasedPart.DoesNotExist:
        return JsonResponse({
            'status': 'error',
            'message': 'Purchased part not found'
        }, status=404)
    except ValueError as e:
        return JsonResponse({
            'status': 'error',
            'message': f'Invalid number format: {str(e)}'
        }, status=400)
    except Exception as e:
        return JsonResponse({
            'status': 'error',
            'message': str(e)
        }, status=400)

@login_required
def delete_purchased_part(request, part_id):
    if request.method == 'POST':
        try:
            part = get_object_or_404(PurchasedPart, id=part_id)
            part.delete()
            return JsonResponse({'status': 'success'})
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)})
    return JsonResponse({'status': 'error', 'message': 'Invalid request method'})

@login_required
def export_project(request, project_id):
    project = get_object_or_404(Project, id=project_id)
    
    # Create a temporary directory to store files
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create subdirectories for different file types
        stl_dir = os.path.join(temp_dir, 'stl_files')
        thumbnail_dir = os.path.join(temp_dir, 'thumbnails')
        instructions_dir = os.path.join(temp_dir, 'instructions')
        project_images_dir = os.path.join(temp_dir, 'project_images')
        
        # Create all directories
        for directory in [stl_dir, thumbnail_dir, instructions_dir, project_images_dir]:
            os.makedirs(directory, exist_ok=True)
        
        # Prepare project data for manifest
        designer_info = None
        if project.designer:
            designer_info = {
                'name': project.designer.name,
                'mmf_url': project.designer.mmf_url,
                'patreon_url': project.designer.patreon_url,
                'cults3d_url': project.designer.cults3d_url,
                'website_url': project.designer.website_url,
            }

        project_data = {
            'metadata': {
                'name': project.name,
                'description': project.description,
                'created_at': project.created_at.isoformat(),
                'updated_at': project.updated_at.isoformat(),
                'designer': project.designer.name if project.designer else None,
                'designer_info': designer_info,
                # Keep designer_id for backward compatibility but it won't be used for imports
                'designer_id': project.designer.id if project.designer else None,
                'tags': [tag.name for tag in project.tags.all()]
            },
            'materials': [],
            'groups': [],
            'parts': [],
            'purchased_parts': [],
            'instructions': [],
            'project_images': []
        }
        
        # Export materials
        materials_used = set()
        for part in project.parts.all():
            if part.material:
                materials_used.add(part.material)
        
        for material in materials_used:
            material_data = {
                'id': material.id,
                'name': material.name,
                'type': material.type,
                'description': material.description,
                'density': float(material.density) if material.density else None,
                'color': material.color,
                'is_active': material.is_active,
                'brand': material.brand,
                'link': material.link,
                'cost': float(material.cost) if material.cost else None,
                'weight': float(material.weight) if material.weight else None
            }
            project_data['materials'].append(material_data)
        
        # Export groups
        for group in project.groups.all():
            group_data = {
                'id': group.id,
                'name': group.name
            }
            project_data['groups'].append(group_data)
        
        # Export parts
        for part in project.parts.all():
            part_data = {
                'id': part.id,
                'name': part.name,
                'quantity': part.quantity,
                'material_id': part.material.id if part.material else None,
                'material_name': part.material_name,
                'color': part.color,
                'completed': part.completed,
                'group_id': part.group.id if part.group else None,
                'volume': float(part.volume) if part.volume else None,
                'material_cost': float(part.material_cost) if part.material_cost else None
            }
            
            # Handle STL file
            if part.stl_file:
                stl_filename = f"{part.name}.stl"
                stl_path = os.path.join(stl_dir, stl_filename)
                with default_storage.open(part.stl_file.name, 'rb') as source_file:
                    with open(stl_path, 'wb') as dest_file:
                        dest_file.write(source_file.read())
                part_data['stl_file'] = f"stl_files/{stl_filename}"
            
            # Handle thumbnail
            if part.thumbnail:
                thumbnail_filename = f"{part.name}.jpg"
                thumbnail_path = os.path.join(thumbnail_dir, thumbnail_filename)
                with default_storage.open(part.thumbnail.name, 'rb') as source_file:
                    with open(thumbnail_path, 'wb') as dest_file:
                        dest_file.write(source_file.read())
                part_data['thumbnail'] = f"thumbnails/{thumbnail_filename}"
            
            project_data['parts'].append(part_data)
        
        # Export purchased parts
        for part in project.purchased_parts.all():
            part_data = {
                'id': part.id,
                'name': part.name,
                'price': float(part.price),
                'quantity': part.quantity,
                'link': part.link,
                'status': part.status
            }
            project_data['purchased_parts'].append(part_data)
        
        # Export instructions
        for instruction in project.instructions.all().order_by('order'):
            instruction_data = {
                'id': instruction.id,
                'description': instruction.description,
                'order': instruction.order
            }
            
            # Handle instruction file
            if instruction.file:
                # Get file extension from original filename
                file_ext = os.path.splitext(instruction.file.name)[1] or '.jpg'
                file_filename = f"step_{instruction.order}{file_ext}"
                file_path = os.path.join(instructions_dir, file_filename)
                with default_storage.open(instruction.file.name, 'rb') as source_file:
                    with open(file_path, 'wb') as dest_file:
                        dest_file.write(source_file.read())
                instruction_data['file'] = f"instructions/{file_filename}"
            
            project_data['instructions'].append(instruction_data)
        
        # Export project images
        for image in project.images.all():
            image_data = {
                'id': image.id,
                'uploaded_at': image.uploaded_at.isoformat()
            }
            
            # Handle project image
            if image.image:
                image_filename = f"project_image_{image.id}.jpg"
                image_path = os.path.join(project_images_dir, image_filename)
                with default_storage.open(image.image.name, 'rb') as source_file:
                    with open(image_path, 'wb') as dest_file:
                        dest_file.write(source_file.read())
                image_data['image'] = f"project_images/{image_filename}"
            
            project_data['project_images'].append(image_data)
        
        # Create manifest file
        manifest_path = os.path.join(temp_dir, 'manifest.json')
        with open(manifest_path, 'w') as f:
            json.dump(project_data, f, indent=2)
        
        # Create zip file with a safe filename
        safe_project_name = "".join(c for c in project.name if c.isalnum() or c in (' ', '-', '_')).strip()
        zip_filename = f"{safe_project_name}.zip"
        zip_path = os.path.join(temp_dir, zip_filename)
        
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(temp_dir):
                for file in files:
                    if file != zip_filename:  # Don't include the zip file itself
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, temp_dir)
                        zipf.write(file_path, arcname)
        
        # Serve the zip file
        with open(zip_path, 'rb') as f:
            response = HttpResponse(f.read(), content_type='application/zip')
            response['Content-Disposition'] = f'attachment; filename="{zip_filename}"'
            return response

def register(request):
    if request.method == 'POST':
        form = UserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(request, 'Registration successful!')
            return redirect('projects:index')
    else:
        form = UserCreationForm()
    return render(request, 'registration/register.html', {'form': form})

@login_required
def delete_part(request, part_id):
    if request.method == 'POST':
        try:
            part = get_object_or_404(Part, id=part_id)
            part.delete()
            return JsonResponse({'status': 'success'})
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)})
    return JsonResponse({'status': 'error', 'message': 'Invalid request method'})

@login_required
def import_project(request):
    if request.method == 'POST':
        try:
            # Get the uploaded zip file
            zip_file = request.FILES.get('zip_file')
            if not zip_file:
                logger.warning("Import project failed: No file uploaded")
                return JsonResponse({
                    'status': 'error',
                    'message': 'No file was uploaded'
                }, status=400)

            # Create a temporary directory to extract files
            with tempfile.TemporaryDirectory() as temp_dir:
                # Extract the zip file
                logger.info(f"Extracting zip file: {zip_file.name}")
                with zipfile.ZipFile(zip_file) as z:
                    z.extractall(temp_dir)

                # Read the manifest file
                manifest_path = os.path.join(temp_dir, 'manifest.json')
                if not os.path.exists(manifest_path):
                    logger.warning("Import project failed: No manifest.json found in zip")
                    return JsonResponse({
                        'status': 'error',
                        'message': 'No manifest.json file found in the zip'
                    }, status=400)

                with open(manifest_path) as f:
                    project_data = json.load(f)
                
                logger.info(f"Importing project: {project_data['metadata']['name']}")

                # Find designer by name if available
                designer = None
                if designer_name := project_data['metadata'].get('designer'):
                    designer = Designer.objects.filter(name=designer_name).first()
                    # Log the designer search result
                    if designer:
                        logger.info(f"Found designer: {designer.name} (ID: {designer.id})")
                    else:
                        logger.info(f"Designer '{designer_name}' not found in database, creating a new one.")
                        designer_info = project_data['metadata'].get('designer_info', {})
                        designer = Designer.objects.create(
                            name=designer_name,
                            mmf_url=designer_info.get('mmf_url', ''),
                            patreon_url=designer_info.get('patreon_url', ''),
                            cults3d_url=designer_info.get('cults3d_url', ''),
                            website_url=designer_info.get('website_url', '')
                        )

                # Create the project
                project = Project.objects.create(
                    name=project_data['metadata']['name'],
                    description=project_data['metadata']['description'],
                    user=request.user,
                    designer=designer  # Use the designer object or None
                )
                
                # Import tags
                for tag_name in project_data['metadata'].get('tags', []):
                    tag, _ = Tag.objects.get_or_create(name=tag_name)
                    project.tags.add(tag)
                logger.info(f"Created project: {project.name} (ID: {project.id})")

                # Create or get materials
                materials = {}
                for material_data in project_data['materials']:
                    # Try to get existing material by name
                    existing_material = Material.objects.filter(name=material_data['name']).first()
                    if existing_material:
                        logger.info(f"Using existing material: {existing_material.name}")
                        materials[material_data['id']] = existing_material
                    else:
                        # Create new material
                        logger.info(f"Creating new material: {material_data['name']}")
                        material = Material.objects.create(
                            name=material_data['name'],
                            type=material_data['type'],
                            description=material_data['description'],
                            density=material_data['density'],
                            color=material_data['color'],
                            is_active=material_data['is_active'],
                            brand=material_data['brand'],
                            link=material_data['link'],
                            cost=material_data['cost'],
                            weight=material_data['weight']
                        )
                        materials[material_data['id']] = material

                # Create groups
                groups = {}
                for group_data in project_data['groups']:
                    group = Group.objects.create(
                        name=group_data['name'],
                        project=project
                    )
                    groups[group_data['id']] = group

                # Create parts
                for part_data in project_data['parts']:
                    part = Part.objects.create(
                        project=project,
                        name=part_data['name'],
                        quantity=part_data['quantity'],
                        material=materials.get(part_data['material_id']),
                        color=part_data['color'],
                        completed=part_data['completed'],
                        group=groups.get(part_data['group_id']),
                        volume=part_data['volume']
                    )

                    # Handle STL file
                    if part_data.get('stl_file'):
                        stl_path = os.path.join(temp_dir, part_data['stl_file'])
                        if os.path.exists(stl_path):
                            with open(stl_path, 'rb') as f:
                                part.stl_file.save(os.path.basename(stl_path), File(f), save=True)

                    # Handle thumbnail
                    if part_data.get('thumbnail'):
                        thumbnail_path = os.path.join(temp_dir, part_data['thumbnail'])
                        if os.path.exists(thumbnail_path):
                            with open(thumbnail_path, 'rb') as f:
                                part.thumbnail.save(os.path.basename(thumbnail_path), File(f), save=True)

                # Create purchased parts
                for part_data in project_data['purchased_parts']:
                    PurchasedPart.objects.create(
                        project=project,
                        name=part_data['name'],
                        price=part_data['price'],
                        quantity=part_data['quantity'],
                        link=part_data['link'],
                        status=part_data['status']
                    )

                # Create instructions
                for instruction_data in project_data['instructions']:
                    instruction = Instructions.objects.create(
                        project=project,
                        description=instruction_data['description'],
                        order=instruction_data['order']
                    )

                    # Handle instruction file
                    if instruction_data.get('file'):
                        file_path = os.path.join(temp_dir, instruction_data['file'])
                        if os.path.exists(file_path):
                            with open(file_path, 'rb') as f:
                                instruction.file.save(os.path.basename(file_path), File(f), save=True)
                    # Backward compatibility: also check for 'image' key
                    elif instruction_data.get('image'):
                        image_path = os.path.join(temp_dir, instruction_data['image'])
                        if os.path.exists(image_path):
                            with open(image_path, 'rb') as f:
                                instruction.file.save(os.path.basename(image_path), File(f), save=True)

                # Create project images
                for image_data in project_data['project_images']:
                    image = ProjectImage.objects.create(
                        project=project,
                        uploaded_at=datetime.fromisoformat(image_data['uploaded_at'])
                    )

                    # Handle project image
                    if image_data.get('image'):
                        image_path = os.path.join(temp_dir, image_data['image'])
                        if os.path.exists(image_path):
                            with open(image_path, 'rb') as f:
                                image.image.save(os.path.basename(image_path), File(f), save=True)

                messages.success(request, 'Project imported successfully!')
                return redirect('projects:project_detail', pk=project.id)

        except Exception as e:
            # Log the full exception with traceback
            logger.exception(f"Error importing project: {str(e)}")
            messages.error(request, f'Error importing project: {str(e)}')
            return redirect('projects:index')

    return render(request, 'projects/import_project.html')

@login_required
def material_delete(request, material_id):
    material = get_object_or_404(Material, id=material_id)
    if request.method == 'POST':
        material.delete()
        messages.success(request, f'Material "{material.name}" was deleted successfully.')
        return redirect('projects:material_list')
    return render(request, 'projects/material_confirm_delete.html', {'object': material})

@login_required
def delete_project_image(request, image_id):
    if request.method == 'POST':
        try:
            image = ProjectImage.objects.get(id=image_id)
            project = image.project
            # Check if user has permission to edit this project
            if project.user != request.user:
                return JsonResponse({'status': 'error', 'message': 'Permission denied'}, status=403)
            
            # Delete the image file from storage
            if image.image:
                if os.path.isfile(image.image.path):
                    os.remove(image.image.path)
            
            # Delete the image record
            image.delete()
            
            return JsonResponse({'status': 'success'})
        except ProjectImage.DoesNotExist:
            return JsonResponse({'status': 'error', 'message': 'Image not found'}, status=404)
    return JsonResponse({'status': 'error', 'message': 'Invalid request'}, status=400)

@login_required
def update_project(request, pk):
    project = get_object_or_404(Project, pk=pk)
    if project.user != request.user:
        return HttpResponseForbidden()
    
    if request.method == 'POST':
        form = ProjectForm(request.POST, request.FILES, instance=project)
        if form.is_valid():
            project = form.save()
            
            # Handle image reordering
            image_order = request.POST.get('image_order')
            if image_order:
                order_list = image_order.split(',')
                for index, image_id in enumerate(order_list):
                    try:
                        image = ProjectImage.objects.get(id=image_id, project=project)
                        image.order = index
                        image.save()
                        
                        # Set the first image as the project thumbnail
                        if index == 0:
                            project.thumbnail = image.image
                            project.save()
                    except ProjectImage.DoesNotExist:
                        continue
            
            # Handle new image uploads
            files = request.FILES
            for key in files:
                if key.startswith('image_'):
                    ProjectImage.objects.create(
                        project=project,
                        image=files[key]
                    )
            
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({
                    'status': 'success',
                    'redirect_url': reverse('projects:project_detail', kwargs={'pk': project.pk})
                })
            else:
                messages.success(request, 'Project updated successfully.')
                return redirect('projects:project_detail', pk=project.pk)
        else:
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({
                    'status': 'error',
                    'message': 'Please correct the errors below.',
                    'errors': form.errors
                }, status=400)
    
    form = ProjectForm(instance=project)
    return render(request, 'projects/project_form.html', {
        'form': form,
        'project': project
    })

@login_required
def set_project_thumbnail(request, image_id):
    if request.method == 'POST':
        try:
            image = ProjectImage.objects.get(id=image_id)
            project = image.project
            # Check if user has permission to edit this project
            if project.user != request.user:
                return JsonResponse({'status': 'error', 'message': 'Permission denied'}, status=403)
            
            # Set the image as the project thumbnail
            project.thumbnail = image.image
            project.save()
            
            return JsonResponse({'status': 'success'})
        except ProjectImage.DoesNotExist:
            return JsonResponse({'status': 'error', 'message': 'Image not found'}, status=404)
    return JsonResponse({'status': 'error', 'message': 'Invalid request'}, status=400)

@login_required
def settings(request):
    user = request.user
    settings_types = ['general', 'appearance', 'api', 'system']
    user_settings_instances = {}
    forms = {}

    # Load existing settings for each type
    for type_name in settings_types:
        instance, _ = UserSettings.objects.get_or_create(
            user=user, 
            settings_type=type_name,
            defaults={'settings_data': json.dumps({})}
        )
        user_settings_instances[type_name] = instance
        try:
            initial_data = json.loads(instance.settings_data)
        except (TypeError, json.JSONDecodeError):
            initial_data = {}
        forms[type_name] = initial_data # Store parsed JSON data

    # Machine management
    machine_form = MachineForm()
    machines = Machine.objects.filter(user=user)
    machine_to_edit = None

    # Get all active materials
    materials = Material.objects.filter(is_active=True).order_by('name')

    if request.method == 'POST':
        settings_type_submitted = request.POST.get('settings_type')
        machine_action = request.POST.get('machine_action')

        if settings_type_submitted in settings_types:
            instance = user_settings_instances[settings_type_submitted]
            current_data = json.loads(instance.settings_data) if instance.settings_data else {}

            if settings_type_submitted == 'general':
                current_data['default_material'] = request.POST.get('default_material')
                current_data['unit_system'] = request.POST.get('unit_system', 'imperial')

            elif settings_type_submitted == 'appearance':
                theme_preference = request.POST.get('theme_preference')
                current_data['theme_preference'] = theme_preference
                if theme_preference:
                    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                        response = JsonResponse({'status': 'success', 'message': f'Theme preference "{theme_preference.capitalize()}" saved.'})
                    else:
                        messages.success(request, f'Theme preference "{theme_preference.capitalize()}" saved.')
                        response = redirect('projects:settings')
                    
                    response.set_cookie('theme_preference', theme_preference, max_age=365 * 24 * 60 * 60) # 1 year
                    instance.settings_data = json.dumps(current_data)
            elif settings_type_submitted == 'api':
                current_data['require_auth'] = request.POST.get('require_auth') == 'on'
                current_data['is_global'] = request.POST.get('is_global') == 'on'
                current_data['enable_slicer_inbox'] = request.POST.get('enable_slicer_inbox') == 'on'
                current_data['auto_complete_prints'] = request.POST.get('auto_complete_prints') == 'on'
                current_data['api_key'] = request.POST.get('api_key', '')
                
            elif settings_type_submitted == 'system':
                log_level = request.POST.get('log_level', 'INFO')
                current_data['log_level'] = log_level
                # Apply immediately
                from projects.logging_utils import apply_system_log_level
                apply_system_log_level(log_level)

            instance.settings_data = json.dumps(current_data)
            instance.save()
            
            if settings_type_submitted == 'appearance':
                return response
                
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'status': 'success', 'message': f'{settings_type_submitted.capitalize()} settings saved successfully.'})
            
            messages.success(request, f'{settings_type_submitted.capitalize()} settings saved successfully.')
            return redirect('projects:settings')
        
        elif machine_action:
            if machine_action == 'add' or machine_action == 'edit':
                machine_id = request.POST.get('machine_id')
                instance = get_object_or_404(Machine, pk=machine_id, user=user) if machine_id else None
                machine_form = MachineForm(request.POST, instance=instance)
                if machine_form.is_valid():
                    machine = machine_form.save(commit=False)
                    machine.user = user
                    machine.save()
                    messages.success(request, f'Machine "{machine.name}" saved successfully.')
                    return redirect('projects:settings')
                else:
                    if instance:
                        machine_to_edit = instance # Keep form populated with errors
            elif machine_action == 'delete':
                machine_id = request.POST.get('machine_id')
                machine = get_object_or_404(Machine, pk=machine_id, user=user)
                machine_name = machine.name
                machine.delete()
                messages.success(request, f'Machine "{machine_name}" deleted successfully.')
                return redirect('projects:settings')

    # Prepare context for GET request or if form is invalid
    if request.GET.get('edit_machine'):
        machine_id = request.GET.get('edit_machine')
        machine_to_edit = get_object_or_404(Machine, pk=machine_id, user=user)
        machine_form = MachineForm(instance=machine_to_edit)

    return render(request, 'projects/settings.html', {
        'forms': forms,
        'appearance_settings': forms.get('appearance', {}),
        'api_settings': forms.get('api', {}),
        'system_settings': forms.get('system', {}),
        'machines': machines,
        'machine_form': machine_form,
        'machine_to_edit': machine_to_edit,
        'materials': materials,
    })

@login_required
def api_system_logs(request):
    import os
    from django.conf import settings as django_settings
    
    log_path = os.path.join(django_settings.BASE_DIR, 'logs', 'modelfoundry.log')
    try:
        if not os.path.exists(log_path):
            return JsonResponse({'status': 'success', 'logs': 'No logs found. File does not exist yet.'})
            
        with open(log_path, 'r', encoding='utf-8') as f:
            # Read last 500 lines to avoid sending too much data
            lines = f.readlines()
            logs = "".join(lines[-500:])
            
        return JsonResponse({'status': 'success', 'logs': logs})
    except Exception as e:
        logger.error(f"Error reading system logs: {e}")
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)

@login_required
def api_machine_logs(request, machine_id):
    import os
    from django.conf import settings as django_settings
    
    # Verify user has access to this machine
    machine = get_object_or_404(Machine, pk=machine_id, user=request.user)
    
    log_path = os.path.join(django_settings.BASE_DIR, 'logs', 'modelfoundry.log')
    try:
        if not os.path.exists(log_path):
            return JsonResponse({'status': 'success', 'logs': 'No logs found. File does not exist yet.'})
            
        machine_identifier = f"[Machine {machine.id}]"
        matched_lines = []
        
        with open(log_path, 'r', encoding='utf-8') as f:
            for line in f:
                if machine_identifier in line:
                    matched_lines.append(line)
            
            # Read last 300 lines of matching logs
            logs = "".join(matched_lines[-300:])
            
        return JsonResponse({'status': 'success', 'logs': logs})
    except Exception as e:
        logger.error(f"Error reading machine logs: {e}")
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)

@login_required
def manage_tags(request, project_id):
    project = get_object_or_404(Project, id=project_id)
    if request.method == 'POST':
        tag_name = request.POST.get('tag_name')
        if tag_name:
            tag, created = Tag.objects.get_or_create(name=tag_name)
            project.tags.add(tag)
            return JsonResponse({'status': 'success', 'message': f'Tag "{tag_name}" added to project'})
        else:
            return JsonResponse({'status': 'error', 'message': 'Tag name is required'})
    return render(request, 'projects/manage_tags.html', {'project': project})

@login_required
@require_POST
def bulk_edit_parts(request, project_id):
    project = get_object_or_404(Project, id=project_id)
    try:
        data = json.loads(request.body)
        part_ids = data.get('part_ids', [])
        if not part_ids:
            return JsonResponse({'status': 'error', 'message': 'No parts selected'}, status=400)
        
        parts = Part.objects.filter(id__in=part_ids, project=project)
        
        material_id = data.get('material_id')
        color = data.get('color')
        group_id = data.get('group_id')
        
        update_fields = {}
        if material_id is not None:
            if material_id == '':
                update_fields['material'] = None
            else:
                material = get_object_or_404(Material, id=material_id)
                update_fields['material'] = material
        if color:
            update_fields['color'] = color
        if group_id is not None:
            if group_id == '':
                update_fields['group'] = None
            else:
                group = get_object_or_404(Group, id=group_id)
                update_fields['group'] = group
                
        if update_fields:
            for part in parts:
                for field, value in update_fields.items():
                    setattr(part, field, value)
                part.save()
                
        return JsonResponse({'status': 'success', 'message': f'Successfully updated {parts.count()} parts'})
    except Exception as e:
        logger.error(f'Error in bulk edit parts: {str(e)}')
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)

@login_required
@require_POST
def bulk_delete_parts(request, project_id):
    project = get_object_or_404(Project, id=project_id)
    try:
        data = json.loads(request.body)
        part_ids = data.get('part_ids', [])
        if not part_ids:
            return JsonResponse({'status': 'error', 'message': 'No parts selected'}, status=400)
            
        parts = Part.objects.filter(id__in=part_ids, project=project)
        count = parts.count()
        parts.delete()
        
        return JsonResponse({'status': 'success', 'message': f'Successfully deleted {count} parts'})
    except Exception as e:
        logger.error(f'Error in bulk delete parts: {str(e)}')
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)

@login_required
@require_POST
def bulk_complete_parts(request, project_id):
    project = get_object_or_404(Project, id=project_id)
    try:
        data = json.loads(request.body)
        part_ids = data.get('part_ids', [])
        if not part_ids:
            return JsonResponse({'status': 'error', 'message': 'No parts selected'}, status=400)
            
        parts = Part.objects.filter(id__in=part_ids, project=project)
        count = 0
        for part in parts:
            part.completed = part.quantity
            part.save()
            count += 1
            
        return JsonResponse({'status': 'success', 'message': f'Successfully marked {count} parts as complete'})
    except Exception as e:
        logger.error(f'Error in bulk complete parts: {str(e)}')
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)

@login_required
def api_ofd_inventory(request):
    """Returns the cached OFD inventory tree"""
    inventory = OFDClient.get_inventory()
    return JsonResponse(inventory, safe=False)

@login_required
def api_ofd_filament(request):
    """Returns the raw JSON for a specific OFD filament"""
    url = request.GET.get('url')
    if not url:
        return JsonResponse({'error': 'Missing URL parameter'}, status=400)
    
    data = OFDClient.get_filament_data(url)
    if not data:
        return JsonResponse({'error': 'Failed to fetch filament data'}, status=500)
        
    return JsonResponse(data)

@csrf_exempt
def api_slicer_sync(request):
    if request.method == 'POST':
        from django.contrib.auth.models import User
        
        provided_key = request.headers.get('X-API-Key')
        user = None
        
        # Try to find user by API key if provided
        if provided_key:
            for us in UserSettings.objects.filter(settings_type='api'):
                try:
                    data = json.loads(us.settings_data)
                    if data.get('api_key') == provided_key:
                        user = us.user
                        break
                except (TypeError, json.JSONDecodeError):
                    continue
        
        # If no user found by key, fallback to first user
        if not user:
            user = User.objects.first()
            
        if not user:
            return JsonResponse({'status': 'error', 'message': 'System not configured with a user'}, status=500)
            
        # Check API Authentication for the resolved user
        api_settings_obj, _ = UserSettings.objects.get_or_create(
            user=user, settings_type='api', defaults={'settings_data': '{}'}
        )
        api_settings = json.loads(api_settings_obj.settings_data) if api_settings_obj.settings_data else {}
        
        if api_settings.get('require_auth'):
            if not provided_key or provided_key != api_settings.get('api_key'):
                return JsonResponse({'status': 'error', 'message': 'Unauthorized. Invalid or missing API key.'}, status=401)
                
        try:
            data = json.loads(request.body)
            
            # Handle connection test ping from desktop agent
            if data.get('test') is True:
                return JsonResponse({'status': 'success', 'message': 'API Connection Successful'})
                
            filename = data.get('filename')
            print_time_seconds = data.get('print_time_seconds')
            filament_weight_g = data.get('filament_weight_g')
            filament_type = data.get('filament_type') or ''

            if not filename:
                logger.error(f"Slicer Sync: Filename missing in payload: {data}")
                return JsonResponse({'status': 'error', 'message': 'Filename is required'}, status=400)

            # Clean filename to bare slug for fuzzy matching
            def clean_name(name):
                # Remove extensions
                name = re.sub(r'\.(gcode|3mf|stl|obj)$', '', name, flags=re.IGNORECASE)
                # Remove non-alphanumeric
                name = re.sub(r'[^a-zA-Z0-9]', '', name).lower()
                return name

            slicer_clean = clean_name(filename)

            matched_part = None
            for part in Part.objects.all():
                if clean_name(part.name) == slicer_clean:
                    matched_part = part
                    break
            
            if matched_part:
                matched_part.print_time_seconds = print_time_seconds
                matched_part.filament_weight_g = filament_weight_g
                # Optionally try to match material in the future
                matched_part.save()
                return JsonResponse({'status': 'success', 'message': f'Auto-linked to {matched_part.name}', 'part_id': matched_part.id})
            
            # If no match, save to UnclaimedSlice
            # Assign to the first user for now in this single-user assumption
            # All other keys in data will be saved as metadata
            metadata = {k: v for k, v in data.items() if k not in ['filename', 'print_time_seconds', 'filament_weight_g', 'filament_type', 'test']}

            UnclaimedSlice.objects.create(
                user=user,
                filename=filename,
                print_time_seconds=print_time_seconds,
                filament_weight_g=filament_weight_g,
                filament_type=filament_type,
                metadata=metadata
            )
            return JsonResponse({'status': 'success', 'message': 'Saved to Slicer Inbox', 'inbox': True})
        except json.JSONDecodeError:
            return JsonResponse({'status': 'error', 'message': 'Invalid JSON'}, status=400)
    return JsonResponse({'status': 'error', 'message': 'Only POST allowed'}, status=405)

@login_required
def api_slicer_inbox_count(request):
    global_users = []
    for us in UserSettings.objects.filter(settings_type='api'):
        try:
            if json.loads(us.settings_data).get('is_global'):
                global_users.append(us.user)
        except (TypeError, json.JSONDecodeError):
            pass
            
    count = UnclaimedSlice.objects.filter(
        Q(user=request.user) | Q(user__in=global_users),
        status='pending'
    ).distinct().count()
    return JsonResponse({'count': count})

@login_required
@require_POST
def test_mqtt_connection(request):
    import paho.mqtt.client as mqtt
    import ssl
    import json
    
    try:
        data = json.loads(request.body)
        ip_address = data.get('ip_address')
        mqtt_access_code = data.get('mqtt_access_code')
        
        if not ip_address or not mqtt_access_code:
            return JsonResponse({'status': 'error', 'message': 'IP Address and Access Code are required'})
            
        # Set up synchronous client
        client = mqtt.Client(protocol=mqtt.MQTTv311)
        client.tls_set(cert_reqs=ssl.CERT_NONE)
        client.tls_insecure_set(True)
        client.username_pw_set("bblp", mqtt_access_code)
        
        # Connect and immediately disconnect
        try:
            client.connect(ip_address, 8883, keepalive=5)
            client.disconnect()
            return JsonResponse({'status': 'success', 'message': 'Successfully connected to printer!'})
        except Exception as conn_e:
            return JsonResponse({'status': 'error', 'message': f'Connection failed: {str(conn_e)}'})
            
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': f'Server error: {str(e)}'})

@login_required
def slicer_inbox(request):
    global_users = []
    for us in UserSettings.objects.filter(settings_type='api'):
        try:
            if json.loads(us.settings_data).get('is_global'):
                global_users.append(us.user)
        except (TypeError, json.JSONDecodeError):
            continue

    unclaimed_slices = UnclaimedSlice.objects.filter(
        Q(user=request.user) | Q(user__in=global_users),
        status='pending'
    ).distinct()
    
    projects = Project.objects.filter(user=request.user).prefetch_related('parts')
    materials = Material.objects.filter(is_active=True).order_by('name')
    
    # Perform intelligent matching for each slice
    for slice_obj in unclaimed_slices:
        metadata = slice_obj.metadata or {}
        vendor = metadata.get('filament_vendor', '').strip().replace('"', '').replace("'", '')
        raw_color = metadata.get('filament_colour', '').strip().replace('"', '').replace("'", '')
        color = get_closest_color_name(raw_color) if raw_color else ""
        f_type = slice_obj.filament_type.strip() if slice_obj.filament_type else metadata.get('filament_type', '').strip()
        density = metadata.get('filament_density')
        cost = metadata.get('filament_cost')

        matched_material = None
        
        # Try to find an exact match first
        if f_type:
            candidates = materials.filter(type__iexact=f_type)
            if vendor:
                candidates = candidates.filter(brand__icontains=vendor)
            if color:
                candidates = candidates.filter(color__iexact=color)
            
            matched_material = candidates.first()
            
        if matched_material:
            slice_obj.matched_material = matched_material
            
        # Always prepare a suggestion in case they want to create a new one anyway
        suggested_name = f"{vendor} {f_type} {color}".strip()
        if not suggested_name:
            suggested_name = f"Unknown {f_type}"
        
        slice_obj.suggested_material = {
            'name': suggested_name,
            'brand': vendor,
            'type': f_type,
            'color': color,
            'density': density,
            'cost': cost
        }
            
    return render(request, 'projects/slicer_inbox.html', {
        'unclaimed_slices': unclaimed_slices,
        'projects': projects,
        'materials': materials,
    })

@login_required
def assign_slice(request, slice_id):
    if request.method == 'POST':
        unclaimed_slice = get_object_or_404(UnclaimedSlice, id=slice_id)
        
        # Check permissions
        is_global = False
        api_setting = UserSettings.objects.filter(user=unclaimed_slice.user, settings_type='api').first()
        if api_setting:
            try:
                is_global = json.loads(api_setting.settings_data).get('is_global', False)
            except (TypeError, json.JSONDecodeError):
                pass
                
        if unclaimed_slice.user != request.user and not is_global:
            return HttpResponseForbidden("You do not have permission to modify this slice.")

        part_id = request.POST.get('part_id')
        material_id = request.POST.get('material_id')
        
        if part_id:
            part = get_object_or_404(Part, id=part_id, project__user=request.user)
            part.print_time_seconds = unclaimed_slice.print_time_seconds
            part.filament_weight_g = unclaimed_slice.filament_weight_g
            
            # Handle material assignment and creation
            metadata = unclaimed_slice.metadata or {}
            
            if material_id == 'create_new':
                # Dynamically create new material
                vendor = metadata.get('filament_vendor', '').strip().replace('"', '').replace("'", "")
                raw_color = metadata.get('filament_colour', '').strip().replace('"', '').replace("'", "")
                color = get_closest_color_name(raw_color) if raw_color else ""
                f_type = unclaimed_slice.filament_type or metadata.get('filament_type', 'PLA')
                
                suggested_name = f"{vendor} {f_type} {color}".strip()
                if not suggested_name:
                    suggested_name = f"Unknown {f_type}"
                    
                # Ensure unique name
                base_name = suggested_name
                counter = 1
                while Material.objects.filter(name=suggested_name).exists():
                    suggested_name = f"{base_name} ({counter})"
                    counter += 1
                    
                new_material = Material.objects.create(
                    name=suggested_name,
                    brand=vendor,
                    type=f_type,
                    color=color,
                    density=metadata.get('filament_density') or None,
                    cost=metadata.get('filament_cost') or 25.00
                )
                
                # Assign the newly created material
                if not part.material:
                    part.material = new_material
                    
            elif material_id:
                # Existing material selected
                try:
                    selected_material = Material.objects.get(id=material_id)
                    
                    # Adopt empty values from slice metadata if the material is missing them
                    updated = False
                    if not selected_material.density and metadata.get('filament_density'):
                        selected_material.density = metadata.get('filament_density')
                        updated = True
                    if not selected_material.cost and metadata.get('filament_cost'):
                        selected_material.cost = metadata.get('filament_cost')
                        updated = True
                    if not selected_material.color and metadata.get('filament_colour'):
                        selected_material.color = metadata.get('filament_colour')
                        updated = True
                    if not selected_material.brand and metadata.get('filament_vendor'):
                        selected_material.brand = metadata.get('filament_vendor')
                        updated = True
                        
                    if updated:
                        selected_material.save()
                        
                    # Assign to part if part doesn't have a material
                    if not part.material:
                        part.material = selected_material
                        
                except Material.DoesNotExist:
                    pass

            if request.POST.get('mark_complete') == 'on':
                part.completed = part.quantity

            part.assigned_slice_filename = unclaimed_slice.filename
            part.save()
            unclaimed_slice.status = 'claimed'
            unclaimed_slice.save()
            messages.success(request, f"Slice data assigned to {part.name}.")
        else:
            messages.error(request, "Please select a part to assign.")
    return redirect('projects:slicer_inbox')

@login_required
def dismiss_slice(request, slice_id):
    unclaimed_slice = get_object_or_404(UnclaimedSlice, id=slice_id)
    
    # Check permissions
    is_global = False
    api_setting = UserSettings.objects.filter(user=unclaimed_slice.user, settings_type='api').first()
    if api_setting:
        try:
            is_global = json.loads(api_setting.settings_data).get('is_global', False)
        except (TypeError, json.JSONDecodeError):
            pass
            
    if unclaimed_slice.user != request.user and not is_global:
        return HttpResponseForbidden("You do not have permission to modify this slice.")

    unclaimed_slice.status = 'dismissed'
    unclaimed_slice.save()
    messages.info(request, "Slice dismissed.")
    return redirect('projects:slicer_inbox')

@login_required
def insights(request):
    from django.core.cache import cache
    
    def compute_insights_data():
        from django.db.models import Sum, Count, F, ExpressionWrapper, DecimalField
        from django.db.models.functions import TruncDate
        from django.utils import timezone
        from datetime import timedelta
        from decimal import Decimal
        from .models import PrintHistory, Project, Part, Material, Machine, Designer, Tag

        # Global Metrics
        total_projects = Project.objects.count()
        total_parts = Part.objects.count()
        total_materials = Material.objects.count()
        total_machines = Machine.objects.count()

        # 1. Backlog & Completion
        part_agg = Part.objects.aggregate(
            total_qty=Sum('quantity'),
            total_comp=Sum('completed')
        )
        total_parts_required = part_agg['total_qty'] or 0
        total_parts_completed = part_agg['total_comp'] or 0
        total_unprinted = total_parts_required - total_parts_completed
        completion_rate = (total_parts_completed / total_parts_required * 100) if total_parts_required > 0 else 0
        
        projects_query = Project.objects.annotate(
            req=Sum('parts__quantity'),
            comp=Sum('parts__completed')
        )
        unstarted_count = sum(1 for p in projects_query if p.req and p.comp == 0)
        completed_count = sum(1 for p in projects_query if p.req and p.comp == p.req)
        in_progress_count = sum(1 for p in projects_query if p.req and p.comp > 0 and p.comp < p.req)

        status_labels = ['Unstarted', 'In Progress', 'Completed']
        status_data = [unstarted_count, in_progress_count, completed_count]

        # 2. Financials
        total_printed_cost = Decimal('0')
        total_purchased_cost = Decimal('0')
        all_projects = Project.objects.prefetch_related('parts__material', 'purchased_parts')
        for proj in all_projects:
            total_printed_cost += Decimal(str(proj.printed_parts_cost))
            total_purchased_cost += Decimal(str(proj.purchased_parts_cost))
            
        total_library_value = total_printed_cost + total_purchased_cost
        avg_project_cost = total_library_value / total_projects if total_projects > 0 else 0

        # 3. Print Time
        time_agg = Part.objects.filter(print_time_seconds__isnull=False).annotate(
            part_time=ExpressionWrapper(F('print_time_seconds') * F('quantity'), output_field=DecimalField())
        ).aggregate(total_time=Sum('part_time'))
        
        total_seconds = int(time_agg['total_time'] or 0)
        total_print_days = total_seconds // 86400
        total_print_hours = (total_seconds % 86400) // 3600
        total_print_time_str = f"{total_print_days}d {total_print_hours}h"
        
        longest_part = Part.objects.filter(print_time_seconds__isnull=False).order_by('-print_time_seconds').first()
        longest_part_time_str = f"{int(longest_part.print_time_seconds // 3600)}h {int((longest_part.print_time_seconds % 3600) // 60)}m" if longest_part and longest_part.print_time_seconds else "N/A"
        
        avg_part_time = Part.objects.filter(print_time_seconds__isnull=False).aggregate(avg=Sum('print_time_seconds'))['avg']
        count_parts_with_time = Part.objects.filter(print_time_seconds__isnull=False).count()
        avg_part_time_seconds = (avg_part_time / count_parts_with_time) if count_parts_with_time and avg_part_time else 0
        avg_part_time_str = f"{int(avg_part_time_seconds // 3600)}h {int((avg_part_time_seconds % 3600) // 60)}m"

        # 4. Tags
        top_tags = Tag.objects.annotate(project_count=Count('projects')).filter(project_count__gt=0).order_by('-project_count')
        tag_labels = [t.name for t in top_tags[:15]]
        tag_data = [t.project_count for t in top_tags[:15]]
        top_tag = top_tags.first()
        tag_coverage = (top_tag.project_count / total_projects * 100) if top_tag and total_projects > 0 else 0

        # Calculate Material Usage
        materials = Material.objects.all()
        filament_by_type = {}
        filament_by_brand = {}
        total_filament_used_g = Decimal('0')

        for mat in materials:
            used = mat.total_used
            if used > 0:
                total_filament_used_g += used
                
                # Group by type
                m_type = mat.get_type_display()
                filament_by_type[m_type] = float(filament_by_type.get(m_type, 0)) + float(used)
                
                # Group by brand
                m_brand = mat.brand if mat.brand else 'Unknown'
                filament_by_brand[m_brand] = float(filament_by_brand.get(m_brand, 0)) + float(used)

        # Convert usage dicts to lists for Chart.js
        type_labels = list(filament_by_type.keys())
        type_data = list(filament_by_type.values())
        
        # Sub-stats for Types
        most_used_type = max(filament_by_type.items(), key=lambda x: x[1]) if filament_by_type else ('None', 0)
        least_used_type = min(filament_by_type.items(), key=lambda x: x[1]) if filament_by_type else ('None', 0)
        total_types_used = len(filament_by_type)
        avg_type_usage = (total_filament_used_g / total_types_used) if total_types_used > 0 else 0
        
        # Sort brands by usage and get top 5, plus 'Other'
        sorted_brands = sorted(filament_by_brand.items(), key=lambda x: x[1], reverse=True)
        brand_labels = [b[0] for b in sorted_brands[:5]]
        brand_data = [b[1] for b in sorted_brands[:5]]
        if len(sorted_brands) > 5:
            brand_labels.append('Other')
            brand_data.append(sum(b[1] for b in sorted_brands[5:]))

        # Sub-stats for Brands
        top_brand = sorted_brands[0] if sorted_brands else ('None', 0)
        least_used_brand = sorted_brands[-1] if sorted_brands else ('None', 0)
        total_brands_used = len(sorted_brands)
        avg_brand_usage = (total_filament_used_g / total_brands_used) if total_brands_used > 0 else 0

        # Creators / Designers
        top_designers = Designer.objects.annotate(project_count=Count('projects')).filter(project_count__gt=0).order_by('-project_count')
        
        designer_labels = [d.name for d in top_designers[:15]]
        designer_data = [d.project_count for d in top_designers[:15]]
        
        top_creator = top_designers.first()
        total_creators = top_designers.count()
        
        if total_projects > 0:
            top_5_count = sum(d.project_count for d in top_designers[:5])
            top_5_coverage = (top_5_count / total_projects) * 100
        else:
            top_5_coverage = 0
            
        avg_projects_per_creator = (total_projects / total_creators) if total_creators > 0 else 0

        # Biggest Projects
        biggest_project_parts = Project.objects.annotate(part_count=Sum('parts__quantity')).order_by('-part_count').first() if Project.objects.exists() else None
        
        biggest_project_volume = max(Project.objects.all(), key=lambda p: sum((part.volume or 0) * part.quantity for part in p.parts.all()), default=None)
        if biggest_project_volume:
            biggest_project_volume.calculated_volume = sum((part.volume or 0) * part.quantity for part in biggest_project_volume.parts.all()) / 1000 # Convert to cm3
            
        biggest_project_cost = max(Project.objects.all(), key=lambda p: p.total_cost, default=None)

        # 5. Printer Fleet (Workhorse Leaderboard & Success Rate)
        total_history = PrintHistory.objects.count()
        failed_history = PrintHistory.objects.filter(status__in=['failed', 'canceled']).count()
        global_success_rate = ((total_history - failed_history) / total_history * 100) if total_history > 0 else 100.0

        workhorse_data = PrintHistory.objects.filter(status='completed').values('machine__name').annotate(count=Count('id')).order_by('-count')
        workhorse_labels = [w['machine__name'] or 'Unknown Printer' for w in workhorse_data[:5]]
        workhorse_counts = [w['count'] for w in workhorse_data[:5]]

        # Busiest Days & Times
        from django.db.models.functions import ExtractWeekDay, ExtractHour
        day_counts = PrintHistory.objects.annotate(weekday=ExtractWeekDay('timestamp')).values('weekday').annotate(count=Count('id')).order_by('-count')
        hour_counts = PrintHistory.objects.annotate(hour=ExtractHour('timestamp')).values('hour').annotate(count=Count('id')).order_by('-count')
        
        # Django WeekDay: 1=Sunday, 2=Monday, ...
        days_map = {1: 'Sunday', 2: 'Monday', 3: 'Tuesday', 4: 'Wednesday', 5: 'Thursday', 6: 'Friday', 7: 'Saturday'}
        busiest_day = days_map.get(day_counts[0]['weekday'], 'Unknown') if day_counts else 'Unknown'
        
        def format_hour(h):
            if h == 0: return "12 AM"
            elif h < 12: return f"{h} AM"
            elif h == 12: return "12 PM"
            else: return f"{h-12} PM"
        
        busiest_time = format_hour(hour_counts[0]['hour']) if hour_counts else 'Unknown'

        # 6. Pile of Shame
        # Most Neglected: 0% completion, oldest
        neglected_projects = [p for p in projects_query if p.req and p.comp == 0]
        neglected_projects.sort(key=lambda x: x.created_at)
        most_neglected = neglected_projects[:5]

        # Fastest Completions: 100% completion, shortest duration between created and updated
        completed_projects = [p for p in projects_query if p.req and p.comp == p.req]
        completed_projects.sort(key=lambda x: (x.updated_at - x.created_at))
        fastest_completions = completed_projects[:5]

        # 7. Hardware Analysis
        from .models import PurchasedPart
        top_hardware = PurchasedPart.objects.values('name').annotate(total_qty=Sum('quantity')).order_by('-total_qty')[:5]
        
        outstanding_orders = PurchasedPart.objects.filter(status__in=['pending', 'ordered', 'shipped'])
        outstanding_orders_count = outstanding_orders.aggregate(total=Sum('quantity'))['total'] or 0
        outstanding_orders_value = outstanding_orders.aggregate(total=Sum(F('price') * F('quantity'), output_field=DecimalField()))['total'] or Decimal('0.00')

        # Time-Series Analytics (Last 6 months by Month)
        from django.db.models.functions import TruncMonth
        import calendar

        today = timezone.now()
        start_month = today.month - 5
        start_year = today.year
        if start_month <= 0:
            start_month += 12
            start_year -= 1
            
        six_months_ago_month = today.replace(year=start_year, month=start_month, day=1, hour=0, minute=0, second=0, microsecond=0)
        
        # Library Growth
        growth_data = Project.objects.filter(created_at__gte=six_months_ago_month) \
            .annotate(month=TruncMonth('created_at')) \
            .values('month') \
            .annotate(count=Count('id')) \
            .order_by('month')
            
        # Burn Rate By Material Type
        # Calculate burn rate using physical volume and material density
        burn_data = PrintHistory.objects.filter(status='completed', timestamp__gte=six_months_ago_month, part__volume__isnull=False, part__material__isnull=False) \
            .annotate(month=TruncMonth('timestamp')) \
            .values('month', 'part__material__type') \
            .annotate(
                total_weight=Sum(
                    ExpressionWrapper(
                        F('part__volume') * F('part__material__density') / 1000.0,
                        output_field=DecimalField()
                    )
                )
            ) \
            .order_by('month')

        growth_dict = {item['month']: item['count'] for item in growth_data if item['month']}
        
        # Build burn dict: { 'PLA': { date: weight, date: weight }, 'PETG': ... }
        burn_dict_by_type = {}
        unique_types = set()
        for item in burn_data:
            if item['month'] and item['part__material__type']:
                m_type = item['part__material__type']
                m_date = item['month']
                unique_types.add(m_type)
                if m_type not in burn_dict_by_type:
                    burn_dict_by_type[m_type] = {}
                burn_dict_by_type[m_type][m_date] = float(item['total_weight'])
        
        month_labels = []
        library_growth_data = []
        
        # Prepare datasets array for Chart.js
        # Assign distinct colors
        colors = ['#ef4444', '#3b82f6', '#10b981', '#f59e0b', '#8b5cf6', '#ec4899', '#06b6d4', '#64748b']
        burn_rate_datasets = []
        
        for i, m_type in enumerate(sorted(unique_types)):
            burn_rate_datasets.append({
                'label': m_type,
                'data': [],
                'borderColor': colors[i % len(colors)],
                'backgroundColor': colors[i % len(colors)] + '20', # 20% opacity
                'borderWidth': 2,
                'tension': 0.4,
                'fill': True
            })
            
        current_month = six_months_ago_month.date()
        end_month = today.date().replace(day=1)
        
        while current_month <= end_month:
            month_labels.append(current_month.strftime('%b %Y'))
            # We must handle the fact that TruncMonth returns a date or datetime
            c_val = growth_dict.get(current_month, growth_dict.get(six_months_ago_month.replace(year=current_month.year, month=current_month.month), 0))
            library_growth_data.append(c_val)
            
            # Append data for each material type
            for dataset in burn_rate_datasets:
                m_type = dataset['label']
                # Search for match
                val = 0.0
                if m_type in burn_dict_by_type:
                    val = burn_dict_by_type[m_type].get(current_month, burn_dict_by_type[m_type].get(six_months_ago_month.replace(year=current_month.year, month=current_month.month), 0.0))
                dataset['data'].append(val)
                
            next_month = current_month.month + 1
            next_year = current_month.year
            if next_month > 12:
                next_month = 1
                next_year += 1
            current_month = current_month.replace(year=next_year, month=next_month)

        # 8. Material Color Distribution & Cost
        materials_with_cost = Material.objects.filter(cost__isnull=False, weight__isnull=False)
        materials_by_cost = sorted(materials_with_cost, key=lambda m: m.cost_per_kg)
        cheapest_material = materials_by_cost[0] if materials_by_cost else None
        most_expensive_material = materials_by_cost[-1] if materials_by_cost else None

        filament_by_color = {}
        for mat in materials:
            used = mat.total_used
            if used > 0:
                m_color = mat.color if mat.color else 'Unknown'
                filament_by_color[m_color] = float(filament_by_color.get(m_color, 0)) + float(used)
                
        sorted_colors = sorted(filament_by_color.items(), key=lambda x: x[1], reverse=True)
        color_labels = [c[0] for c in sorted_colors[:6]]
        color_data = [c[1] for c in sorted_colors[:6]]

        # Print History Timeline (Last 30 Days)
        thirty_days_ago = timezone.now() - timedelta(days=30)
        history = PrintHistory.objects.filter(status='completed', timestamp__gte=thirty_days_ago)
        prints_by_date = history.annotate(date=TruncDate('timestamp')).values('date').annotate(count=Count('id')).order_by('date')
        
        timeline_labels = []
        timeline_data = []
        
        if prints_by_date:
            date_dict = {p['date']: p['count'] for p in prints_by_date}
            current_date = thirty_days_ago.date()
            end_date = timezone.now().date()
            while current_date <= end_date:
                timeline_labels.append(current_date.strftime('%b %d'))
                timeline_data.append(date_dict.get(current_date, 0))
                current_date += timedelta(days=1)

        # GitHub Style Heatmap Data (Last 6 months)
        six_months_ago = timezone.now() - timedelta(days=180)
        heatmap_history = PrintHistory.objects.filter(status='completed', timestamp__gte=six_months_ago)
        heatmap_by_date = heatmap_history.annotate(date=TruncDate('timestamp')).values('date').annotate(count=Count('id')).order_by('date')
        
        heatmap_dict = {p['date'].strftime('%Y-%m-%d'): p['count'] for p in heatmap_by_date}
        start_date = six_months_ago.date()
        start_date = start_date - timedelta(days=start_date.weekday() + 1 if start_date.weekday() != 6 else 0)
        
        heatmap_grid = []
        current = start_date
        end_date = timezone.now().date()
        
        while current <= end_date:
            week = []
            for _ in range(7):
                if current <= end_date:
                    date_str = current.strftime('%Y-%m-%d')
                    count = heatmap_dict.get(date_str, 0)
                    level = 0
                    if count > 0:
                        if count <= 2: level = 1
                        elif count <= 5: level = 2
                        elif count <= 10: level = 3
                        else: level = 4
                    
                    week.append({
                        'date': date_str,
                        'count': count,
                        'level': level,
                        'month': current.strftime('%b') if current.day <= 7 else ''
                    })
                else:
                    week.append(None)
                current += timedelta(days=1)
            heatmap_grid.append(week)

        # Format Filament Used
        if total_filament_used_g >= 1000:
            total_filament_used_str = f"{float(total_filament_used_g) / 1000:.2f} kg"
        else:
            total_filament_used_str = f"{float(total_filament_used_g):.0f} g"
            
        spools_used = float(total_filament_used_g) / 1000.0

        import json
        return {
            'total_projects': total_projects,
            'total_parts': total_parts,
            'total_materials': total_materials,
            'total_machines': total_machines,
            'total_unprinted': total_unprinted,
            'completion_rate': float(completion_rate),
            'status_labels': json.dumps(status_labels),
            'status_data': json.dumps(status_data),
            'total_library_value': float(total_library_value),
            'total_printed_cost': float(total_printed_cost),
            'total_purchased_cost': float(total_purchased_cost),
            'avg_project_cost': float(avg_project_cost),
            'total_print_time_str': total_print_time_str,
            'avg_part_time_str': avg_part_time_str,
            'longest_part': longest_part,
            'longest_part_time_str': longest_part_time_str,
            'tag_labels': json.dumps(tag_labels),
            'tag_data': json.dumps(tag_data),
            'top_tag': top_tag,
            'tag_coverage': float(tag_coverage),
            'total_filament_used_g': float(total_filament_used_g),
            'total_filament_used_str': total_filament_used_str,
            'spools_used': float(spools_used),
            'type_labels': json.dumps(type_labels),
            'type_data': json.dumps(type_data),
            'most_used_type': most_used_type,
            'least_used_type': least_used_type,
            'total_types_used': total_types_used,
            'avg_type_usage': float(avg_type_usage),
            'brand_labels': json.dumps(brand_labels),
            'brand_data': json.dumps(brand_data),
            'top_brand': top_brand,
            'least_used_brand': least_used_brand,
            'total_brands_used': total_brands_used,
            'avg_brand_usage': float(avg_brand_usage),
            'designer_labels': json.dumps(designer_labels),
            'designer_data': json.dumps(designer_data),
            'top_creator': top_creator,
            'total_creators': total_creators,
            'top_5_coverage': float(top_5_coverage),
            'avg_projects_per_creator': float(avg_projects_per_creator),
            'biggest_project_parts': biggest_project_parts,
            'biggest_project_volume': biggest_project_volume,
            'biggest_project_cost': biggest_project_cost,
            'timeline_labels': json.dumps(timeline_labels),
            'timeline_data': json.dumps(timeline_data),
            'heatmap_grid': heatmap_grid,
            'global_success_rate': float(global_success_rate),
            'workhorse_labels': json.dumps(workhorse_labels),
            'workhorse_counts': json.dumps(workhorse_counts),
            'busiest_day': busiest_day,
            'busiest_time': busiest_time,
            'most_neglected': most_neglected,
            'fastest_completions': fastest_completions,
            'top_hardware': top_hardware,
            'cheapest_material': cheapest_material,
            'most_expensive_material': most_expensive_material,
            'color_labels': json.dumps(color_labels),
            'color_data': json.dumps(color_data),
            'outstanding_orders_count': outstanding_orders_count,
            'outstanding_orders_value': float(outstanding_orders_value),
            'month_labels': json.dumps(month_labels),
            'library_growth_data': json.dumps(library_growth_data),
            'burn_rate_datasets': json.dumps(burn_rate_datasets),
        }

    # Cache for 15 minutes (900 seconds)
    context = cache.get_or_set('insights_data', compute_insights_data, timeout=900)

    return render(request, 'projects/insights.html', context)

def first_setup(request):
    """
    Wizard view to create the first admin user.
    Redirects to login if any users already exist.
    """
    if User.objects.exists():
        return redirect('login')
        
    from .forms import FirstSetupForm
    
    if request.method == 'POST':
        form = FirstSetupForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(request, 'Welcome to ModelFoundry! Your admin account has been created.')
            return redirect('projects:index')
    else:
        form = FirstSetupForm()
        
    return render(request, 'registration/setup.html', {'form': form})
