from django.test import TestCase, SimpleTestCase, Client, override_settings
from django.urls import reverse
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from .models import Project, Part, Group, Collection, PurchasedPart, Material, Designer, Instructions, ProjectImage, Machine
import io
import json
import math
import os
import tempfile
import zipfile
from decimal import Decimal
import numpy as np
from django.core.files import File
from django.core.files.base import ContentFile
from django.template.loader import render_to_string
from unittest.mock import patch, Mock

class ViewTests(TestCase):
    def setUp(self):
        # Create test user
        self.user = User.objects.create_user(
            username='testuser',
            password='testpass123'
        )
        self.client = Client()
        self.client.login(username='testuser', password='testpass123')

        # Create test designer
        self.designer = Designer.objects.create(
            name='Test Designer',
            mmf_url='https://mmf.com/test',
            patreon_url='https://patreon.com/test',
            cults3d_url='https://cults3d.com/test',
            website_url='https://test.com'
        )

        # Create test material
        self.material = Material.objects.create(
            name='Test Material',
            type='PLA',
            description='Test Description',
            density=Decimal('1.25'),
            color='#FF0000',
            is_active=True,
            weight=Decimal('1000.00'),
            cost=Decimal('25.00')
        )

        # Create test project
        self.project = Project.objects.create(
            name='Test Project',
            description='Test Description',
            user=self.user,
            designer=self.designer
        )

        # Create test group
        self.group = Group.objects.create(
            name='Test Group',
            project=self.project
        )

        # Create test part
        self.part = Part.objects.create(
            name='Test Part',
            quantity=1,
            material=self.material,
            project=self.project,
            group=self.group,
            volume=1000.0
        )

        # Create test purchased part
        self.purchased_part = PurchasedPart.objects.create(
            name='Test Purchased Part',
            price=Decimal('10.00'),
            quantity=1,
            project=self.project,
            status='pending'
        )

    def test_index_view(self):
        response = self.client.get(reverse('projects:index'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('projects:project_list'))

    def test_project_list_view(self):
        response = self.client.get(reverse('projects:project_list'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'projects/project_list.html')
        self.assertIn('projects', response.context)

    def test_project_detail_view(self):
        response = self.client.get(reverse('projects:project_detail', args=[self.project.id]))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'projects/project_detail.html')
        self.assertIn('project', response.context)
        self.assertIn('parts', response.context)
        self.assertIn('purchased_parts', response.context)
        self.assertIn('groups', response.context)
        self.assertIn('materials', response.context)
        self.assertIn('material_colors', response.context)
        self.assertIn('instructions', response.context)

    def test_project_create_view(self):
        response = self.client.get(reverse('projects:project_create'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'projects/project_form.html')
        self.assertIn('form', response.context)

        # Test POST request
        response = self.client.post(reverse('projects:project_create'), {
            'name': 'New Project',
            'description': 'New Description',
            'designer': self.designer.id
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Project.objects.filter(name='New Project').exists())

    def test_project_update_view(self):
        response = self.client.get(reverse('projects:project_update', args=[self.project.id]))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'projects/project_form.html')
        self.assertIn('form', response.context)

        # Test POST request
        response = self.client.post(reverse('projects:project_update', args=[self.project.id]), {
            'name': 'Updated Project',
            'description': 'Updated Description',
            'designer': self.designer.id
        })
        self.assertEqual(response.status_code, 302)
        self.project.refresh_from_db()
        self.assertEqual(self.project.name, 'Updated Project')

    def test_project_delete_view(self):
        response = self.client.get(reverse('projects:project_delete', args=[self.project.id]))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'projects/project_confirm_delete.html')

        # Test POST request
        response = self.client.post(reverse('projects:project_delete', args=[self.project.id]))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Project.objects.filter(id=self.project.id).exists())

    def test_add_part_view(self):
        # Create a temporary part form template
        template_content = """
        {% extends 'projects/base.html' %}
        {% block content %}
        <form method="post">
            {% csrf_token %}
            {{ form.as_p }}
            <button type="submit">Save</button>
        </form>
        {% endblock %}
        """
        with open('projects/templates/projects/part_form.html', 'w') as f:
            f.write(template_content)

        response = self.client.get(reverse('projects:add_part', args=[self.project.id]))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'projects/part_form.html')
        self.assertIn('form', response.context)

        # Clean up the temporary template
        os.remove('projects/templates/projects/part_form.html')

    def test_update_part_completion_view(self):
        response = self.client.post(reverse('projects:update_part_completion', args=[self.part.id]), {
            'completed': 1
        })
        self.assertEqual(response.status_code, 200)
        self.part.refresh_from_db()
        self.assertEqual(self.part.completed, 1)

    def test_update_part_group_view(self):
        new_group = Group.objects.create(
            name='New Group',
            project=self.project
        )
        response = self.client.post(reverse('projects:update_part_group', args=[self.part.id]), {
            'group_id': new_group.id
        })
        self.assertEqual(response.status_code, 200)
        self.part.refresh_from_db()
        self.assertEqual(self.part.group, new_group)

    def test_add_group_view(self):
        response = self.client.post(reverse('projects:add_group', args=[self.project.id]), {
            'name': 'New Group'
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(Group.objects.filter(name='New Group').exists())

    def test_add_purchased_part_view(self):
        response = self.client.get(reverse('projects:add_purchased_part', args=[self.project.id]))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'projects/purchased_part_form.html')
        self.assertIn('form', response.context)

        # Test POST request
        response = self.client.post(reverse('projects:add_purchased_part', args=[self.project.id]), {
            'name': 'New Purchased Part',
            'price': '15.00',
            'quantity': 2,
            'status': 'pending'
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(PurchasedPart.objects.filter(name='New Purchased Part').exists())

    def test_export_project_view(self):
        response = self.client.get(reverse('projects:export_project', args=[self.project.id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/zip')
        self.assertTrue(response['Content-Disposition'].startswith('attachment'))

    def test_import_project_view(self):
        # Create a temporary directory for the test zip file
        with tempfile.TemporaryDirectory() as temp_dir:
            zip_path = os.path.join(temp_dir, 'test.zip')
            
            # Create the zip file
            with zipfile.ZipFile(zip_path, 'w') as zf:
                # Create manifest.json
                manifest = {
                    'metadata': {
                        'name': 'Imported Project',
                        'description': 'Imported Description',
                        'designer_id': self.designer.id
                    },
                    'materials': [{
                        'id': 1,
                        'name': 'Test Material',
                        'type': 'PLA',
                        'description': 'Test Description',
                        'density': 1.25,
                        'color': '#FF0000',
                        'is_active': True,
                        'weight': 1000.00,
                        'cost': 25.00
                    }],
                    'groups': [{
                        'id': 1,
                        'name': 'Test Group'
                    }],
                    'parts': [{
                        'name': 'Test Part',
                        'quantity': 1,
                        'material_id': 1,
                        'color': '#FF0000',
                        'completed': 0,
                        'group_id': 1,
                        'volume': 1000.0
                    }],
                    'purchased_parts': [{
                        'name': 'Test Purchased Part',
                        'price': 10.00,
                        'quantity': 1,
                        'link': '',
                        'status': 'pending'
                    }],
                    'instructions': [],
                    'project_images': []
                }
                zf.writestr('manifest.json', json.dumps(manifest))

            # Test POST request with zip file
            with open(zip_path, 'rb') as f:
                response = self.client.post(reverse('projects:import_project'), {
                    'zip_file': SimpleUploadedFile('test.zip', f.read())
                })
            self.assertEqual(response.status_code, 302)
            self.assertTrue(Project.objects.filter(name='Imported Project').exists())

    def _import_manifest(self, manifest):
        with tempfile.TemporaryDirectory() as temp_dir:
            zip_path = os.path.join(temp_dir, 'test.zip')
            with zipfile.ZipFile(zip_path, 'w') as zf:
                zf.writestr('manifest.json', json.dumps(manifest))
            with open(zip_path, 'rb') as f:
                return self.client.post(reverse('projects:import_project'), {
                    'zip_file': SimpleUploadedFile('test.zip', f.read())
                })

    def _base_manifest(self, parts, purchased_parts=None):
        return {
            'metadata': {
                'name': 'Imported Project',
                'description': 'Imported Description'
            },
            'materials': [{
                'id': 1,
                'name': 'Test Material',
                'type': 'PLA',
                'description': 'Test Description',
                'density': 1.25,
                'color': '#FF0000',
                'is_active': True,
                'weight': 1000.00,
                'cost': 25.00
            }],
            'groups': [{
                'id': 1,
                'name': 'Test Group'
            }],
            'parts': parts,
            'purchased_parts': purchased_parts or [],
            'instructions': [],
            'project_images': []
        }

    def test_import_project_quantity_fallback_from_filename(self):
        # No quantity in manifest; file name carries an _x4 token
        manifest = self._base_manifest(parts=[{
            'name': 'Gear',
            'material_id': 1,
            'color': '#FF0000',
            'completed': 0,
            'group_id': 1,
            'volume': 1000.0,
            'stl_file': 'stl_files/gear_x4.stl'
        }])
        response = self._import_manifest(manifest)
        self.assertEqual(response.status_code, 302)
        part = Part.objects.get(name='Gear')
        self.assertEqual(part.quantity, 4)

    def test_import_project_quantity_defaults_to_one(self):
        # No quantity anywhere -> defaults to 1 instead of failing the import
        manifest = self._base_manifest(
            parts=[{
                'name': 'Plain Gear',
                'material_id': 1,
                'color': '#FF0000',
                'completed': 0,
                'group_id': 1,
                'volume': 1000.0
            }],
            purchased_parts=[{
                'name': 'Screws',
                'price': 5.00,
                'link': '',
                'status': 'pending'
            }]
        )
        response = self._import_manifest(manifest)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Part.objects.get(name='Plain Gear').quantity, 1)
        self.assertEqual(PurchasedPart.objects.get(name='Screws').quantity, 1)

    def test_register_view(self):
        response = self.client.get(reverse('projects:register'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'registration/register.html')
        self.assertIn('form', response.context)

        # Test POST request
        response = self.client.post(reverse('projects:register'), {
            'username': 'newuser',
            'password1': 'testpass123',
            'password2': 'testpass123'
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(User.objects.filter(username='newuser').exists())

    def test_upload_project_images_view(self):
        # Create a test image file
        image = SimpleUploadedFile(
            name='test_image.jpg',
            content=b'',
            content_type='image/jpeg'
        )

        # Test POST request
        response = self.client.post(reverse('projects:upload_project_images', args=[self.project.id]), {
            'image_0': image
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(ProjectImage.objects.filter(project=self.project).exists())

    def test_upload_instructions_view(self):
        # Create a test image file
        image = SimpleUploadedFile(
            name='test_instruction.jpg',
            content=b'',
            content_type='image/jpeg'
        )

        # Test POST request with both image and description
        response = self.client.post(reverse('projects:upload_instructions', args=[self.project.id]), {
            'description_format': 'step',
            'images': [image]  # Send as a list of images
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(Instructions.objects.filter(project=self.project).exists())

    def test_delete_instruction_view(self):
        # Create a test instruction
        instruction = Instructions.objects.create(
            project=self.project,
            description='Test Instruction',
            order=1
        )

        # Test POST request
        response = self.client.post(reverse('projects:delete_instruction', args=[self.project.id]), {
            'instruction_id': instruction.id
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Instructions.objects.filter(id=instruction.id).exists())

    def test_part_details_view(self):
        response = self.client.get(reverse('projects:part_details', args=[self.part.id]))
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data['name'], self.part.name)
        self.assertEqual(data['quantity'], self.part.quantity)
        self.assertEqual(data['material_id'], self.part.material.id)

    def test_update_part_view(self):
        response = self.client.post(reverse('projects:update_part', args=[self.part.id]), {
            'name': 'Updated Part',
            'quantity': 3,
            'material_id': self.material.id,
            'color': '#00FF00',
            'group': self.group.id
        })
        self.assertEqual(response.status_code, 200)
        self.part.refresh_from_db()
        self.assertEqual(self.part.name, 'Updated Part')
        self.assertEqual(self.part.quantity, 3)

    def test_delete_part_view(self):
        response = self.client.post(reverse('projects:delete_part', args=[self.part.id]))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Part.objects.filter(id=self.part.id).exists())

    def test_update_part_progress_view(self):
        # First update the part's quantity to allow for higher completion value
        self.part.quantity = 3
        self.part.save()
        
        response = self.client.post(reverse('projects:update_part_progress', args=[self.part.id]), {
            'completed': 2
        })
        self.assertEqual(response.status_code, 200)
        self.part.refresh_from_db()
        self.assertEqual(self.part.completed, 2)

    def test_update_purchased_part_status_view(self):
        response = self.client.post(reverse('projects:update_purchased_part_status', args=[self.purchased_part.id]), {
            'status': 'ordered'
        })
        self.assertEqual(response.status_code, 200)
        self.purchased_part.refresh_from_db()
        self.assertEqual(self.purchased_part.status, 'ordered')

    def test_purchased_part_details_view(self):
        response = self.client.get(reverse('projects:purchased_part_details', args=[self.purchased_part.id]))
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data['name'], self.purchased_part.name)
        self.assertEqual(float(data['price']), float(self.purchased_part.price))
        self.assertEqual(data['quantity'], self.purchased_part.quantity)

    def test_update_purchased_part_view(self):
        response = self.client.post(reverse('projects:update_purchased_part', args=[self.purchased_part.id]), {
            'name': 'Updated Purchased Part',
            'price': '20.00',
            'quantity': 3,
            'link': 'https://example.com',
            'status': 'ordered'
        })
        self.assertEqual(response.status_code, 200)
        self.purchased_part.refresh_from_db()
        self.assertEqual(self.purchased_part.name, 'Updated Purchased Part')
        self.assertEqual(self.purchased_part.price, Decimal('20.00'))
        self.assertEqual(self.purchased_part.quantity, 3)

    def test_delete_purchased_part_view(self):
        response = self.client.post(reverse('projects:delete_purchased_part', args=[self.purchased_part.id]))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PurchasedPart.objects.filter(id=self.purchased_part.id).exists())

    def test_download_stl_view(self):
        # Create a test STL file
        stl_content = b'Test STL content'
        self.part.stl_file.save('test.stl', ContentFile(stl_content))
        
        response = self.client.get(reverse('projects:download_stl', args=[self.part.id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/sla')
        self.assertTrue(response['Content-Disposition'].startswith('attachment'))

class ModelTests(TestCase):
    def setUp(self):
        # Create test user
        self.user = User.objects.create_user(
            username='testuser',
            password='testpass123'
        )

        # Create test designer
        self.designer = Designer.objects.create(
            name='Test Designer',
            mmf_url='https://mmf.com/test',
            patreon_url='https://patreon.com/test',
            cults3d_url='https://cults3d.com/test',
            website_url='https://test.com'
        )

        # Create test material
        self.material = Material.objects.create(
            name='Test Material',
            type='PLA',
            description='Test Description',
            density=Decimal('1.25'),
            color='#FF0000',
            is_active=True,
            weight=Decimal('1000.00'),
            cost=Decimal('25.00')
        )

        # Create test project
        self.project = Project.objects.create(
            name='Test Project',
            description='Test Description',
            user=self.user,
            designer=self.designer
        )

        # Create test group
        self.group = Group.objects.create(
            name='Test Group',
            project=self.project
        )

        # Create test part
        self.part = Part.objects.create(
            name='Test Part',
            quantity=2,
            material=self.material,
            project=self.project,
            group=self.group,
            volume=1000.0,
            completed=1
        )

        # Create test purchased part
        self.purchased_part = PurchasedPart.objects.create(
            name='Test Purchased Part',
            price=Decimal('10.00'),
            quantity=2,
            project=self.project,
            status='pending'
        )

    def test_project_total_parts(self):
        # Test total_parts property
        self.assertEqual(self.project.total_parts, 2)  # One part with quantity 2

    def test_project_material_counts(self):
        # Test material_counts property
        counts = self.project.material_counts
        self.assertEqual(len(counts), 1)
        self.assertEqual(counts[self.material], 2)  # One part with quantity 2

    def test_project_total_cost(self):
        # Test total_cost property
        # Material cost: 1000mm³ * 1.25g/cm³ * $25/kg = $0.03125
        # Purchased part cost: $10 * 2 = $20
        # Total: $20.0625 (since we're using total quantity, not completed)
        expected_cost = 20.0625
        self.assertAlmostEqual(self.project.total_cost, expected_cost, places=4)

    def test_material_cost_per_kg(self):
        # Test cost_per_kg property
        # $25 for 1000g = $25/kg
        self.assertEqual(self.material.cost_per_kg, Decimal('25.00'))

    def test_material_total_used(self):
        # Test total_used property
        # Volume: 1000mm³ = 1cm³
        # Density: 1.25g/cm³
        # Quantity: 2
        # Completed: 1
        # Expected: 1cm³ * 1.25g/cm³ * 1 = 1.25g (based on completed quantity)
        self.assertEqual(self.material.total_used, Decimal('1.250'))

    def test_part_material_cost(self):
        # Test material_cost property
        # Volume: 1000mm³ = 1cm³
        # Density: 1.25g/cm³
        # Cost: $25/kg
        # Expected: 1cm³ * 1.25g/cm³ * $25/kg = $0.03125
        self.assertAlmostEqual(self.part.material_cost, 0.03125, places=5)

    def test_part_calculate_volume(self):
        # Test calculate_volume method
        # Create a test STL file
        stl_content = b'Test STL content'
        self.part.stl_file.save('test.stl', ContentFile(stl_content))
        
        # The method returns 0 for invalid STL files
        self.assertEqual(self.part.calculate_volume(), 0)

    def test_instructions_str(self):
        # Test Instructions __str__ method
        instruction = Instructions.objects.create(
            project=self.project,
            description='Test Instruction',
            order=1
        )
        self.assertEqual(str(instruction), f"Step 1 - {self.project.name}")

    def test_designer_str(self):
        # Test Designer __str__ method
        self.assertEqual(str(self.designer), 'Test Designer')

    def test_group_str(self):
        # Test Group __str__ method
        self.assertEqual(str(self.group), 'Test Group')

    def test_part_str(self):
        # Test Part __str__ method
        self.assertEqual(str(self.part), 'Test Part')

    def test_purchased_part_str(self):
        # Test PurchasedPart __str__ method
        self.assertEqual(str(self.purchased_part), 'Test Purchased Part')

    def test_project_image_str(self):
        # Test ProjectImage __str__ method
        image = ProjectImage.objects.create(
            project=self.project,
            image=SimpleUploadedFile('test.jpg', b'')
        )
        self.assertEqual(str(image), f"Image for {self.project.name}")

    def test_material_str(self):
        # Test Material __str__ method
        self.assertEqual(str(self.material), f"{self.material.name} ({self.material.get_type_display()})")

    def test_project_str(self):
        # Test Project __str__ method
        self.assertEqual(str(self.project), 'Test Project')

    def test_material_ordering(self):
        # Test Material Meta ordering
        material2 = Material.objects.create(
            name='Another Material',
            type='ABS',
            description='Another Description',
            density=Decimal('1.05'),
            color='#00FF00',
            is_active=True,
            weight=Decimal('1000.00'),
            cost=Decimal('30.00')
        )
        materials = list(Material.objects.all())
        # Check that materials are ordered by name. Because of migration data, ABS might be present.
        names = [m.name for m in materials]
        self.assertTrue('Another Material' in names)
        self.assertTrue('Test Material' in names)
        # Verify ordering is alphabetical
        self.assertEqual(names, sorted(names))

    def test_instructions_ordering(self):
        # Test Instructions Meta ordering
        instruction1 = Instructions.objects.create(
            project=self.project,
            description='First Instruction',
            order=2
        )
        instruction2 = Instructions.objects.create(
            project=self.project,
            description='Second Instruction',
            order=1
        )
        instructions = list(Instructions.objects.all())
        self.assertEqual(instructions[0], instruction2)  # Order 1 comes before Order 2

    def test_designer_ordering(self):
        # Test Designer Meta ordering
        designer2 = Designer.objects.create(
            name='Another Designer',
            mmf_url='https://mmf.com/another',
            patreon_url='https://patreon.com/another',
            cults3d_url='https://cults3d.com/another',
            website_url='https://another.com'
        )
        designers = list(Designer.objects.all())
        # Check that designers are ordered by name
        self.assertEqual(designers[0].name, 'Another Designer')  # 'A' comes before 'T'
        self.assertEqual(designers[1].name, 'Test Designer')

class ProjectActionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser',
            password='testpass123'
        )
        self.client = Client()
        self.client.login(username='testuser', password='testpass123')
        self.project = Project.objects.create(
            name='Test Project',
            user=self.user
        )
        self.part1 = Part.objects.create(
            name='Test Part 1',
            project=self.project,
            volume=None
        )
        self.part1.stl_file.name = 'stl_files/test1.stl'
        self.part1.save()

    def test_project_generate_thumbnails_trigger(self):
        from unittest.mock import patch
        with patch('forge.services.blender_client.BlenderClient.is_available', return_value=True):
            response = self.client.post(reverse('projects:project_generate_thumbnails', args=[self.project.id]))
            self.assertEqual(response.status_code, 200)
            data = json.loads(response.content)
            self.assertEqual(data['status'], 'success')
            self.assertIn('task_id', data)
            self.assertEqual(data['total_parts'], 1)

    def test_project_generate_thumbnails_offline(self):
        from unittest.mock import patch
        with patch('forge.services.blender_client.BlenderClient.is_available', return_value=False):
            response = self.client.post(reverse('projects:project_generate_thumbnails', args=[self.project.id]))
            self.assertEqual(response.status_code, 503)
            data = json.loads(response.content)
            self.assertEqual(data['status'], 'error')
            self.assertIn('offline', data['message'].lower())

    def test_project_calculate_volumes_trigger(self):
        response = self.client.post(reverse('projects:project_calculate_volumes', args=[self.project.id]))
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data['status'], 'success')
        self.assertIn('task_id', data)
        self.assertEqual(data['total_parts'], 1)

    def test_project_actions_no_parts(self):
        from unittest.mock import patch
        with patch('forge.services.blender_client.BlenderClient.is_available', return_value=True):
            empty_project = Project.objects.create(
                name='Empty Project',
                user=self.user
            )
            response = self.client.post(reverse('projects:project_generate_thumbnails', args=[empty_project.id]))
            self.assertEqual(response.status_code, 400)


class SmartImportParserTests(SimpleTestCase):
    def test_parse_quantity_x_suffix(self):
        from .smart_import import parse_quantity_from_name
        self.assertEqual(parse_quantity_from_name('bracket_x3'), ('bracket', 3))
        self.assertEqual(parse_quantity_from_name('bracket x3'), ('bracket', 3))
        self.assertEqual(parse_quantity_from_name('bracket-x3'), ('bracket', 3))
        self.assertEqual(parse_quantity_from_name('BRACKET_X3'), ('BRACKET', 3))

    def test_parse_quantity_nx_suffix(self):
        from .smart_import import parse_quantity_from_name
        self.assertEqual(parse_quantity_from_name('bracket_3x'), ('bracket', 3))
        self.assertEqual(parse_quantity_from_name('bracket 12x'), ('bracket', 12))

    def test_parse_quantity_pcs_suffix(self):
        from .smart_import import parse_quantity_from_name
        self.assertEqual(parse_quantity_from_name('bracket_3pcs'), ('bracket', 3))
        self.assertEqual(parse_quantity_from_name('bracket_3pc'), ('bracket', 3))
        self.assertEqual(parse_quantity_from_name('bracket_3 pieces'), ('bracket', 3))

    def test_parse_quantity_no_match(self):
        from .smart_import import parse_quantity_from_name
        # Parentheses are NOT a quantity marker (duplicate downloads)
        self.assertEqual(parse_quantity_from_name('bracket (2)'), ('bracket (2)', 1))
        # Version numbers and unseparated tokens are left alone
        self.assertEqual(parse_quantity_from_name('v2_bracket'), ('v2_bracket', 1))
        self.assertEqual(parse_quantity_from_name('partx3'), ('partx3', 1))
        self.assertEqual(parse_quantity_from_name('plain_name'), ('plain_name', 1))

    def test_parse_quantity_safety(self):
        from .smart_import import parse_quantity_from_name
        # Stripping the token would empty the name -> keep it, qty 1
        self.assertEqual(parse_quantity_from_name('x3'), ('x3', 1))
        # Zero quantity is invalid -> keep original name, qty 1
        self.assertEqual(parse_quantity_from_name('bracket_x0'), ('bracket_x0', 1))

    def test_parse_quantity_ignores_mirror_and_side_tokens(self):
        from .smart_import import parse_quantity_from_name
        # Mirror token after the quantity must not block parsing
        self.assertEqual(parse_quantity_from_name('earcup_x2_mirror'), ('earcup', 2))
        # Side marker after the quantity: parsed and reattached
        self.assertEqual(parse_quantity_from_name('earcup mirror x2 (Left)'), ('earcup (Left)', 2))
        # Quantity between side marker and mirror token
        self.assertEqual(parse_quantity_from_name('earcup (Left) mirror x2'), ('earcup (Left)', 2))
        # No quantity: original name kept (mirror token preserved for downstream)
        self.assertEqual(parse_quantity_from_name('earcup mirror'), ('earcup mirror', 1))

    def test_clean_import_name_with_mirror_token(self):
        from .smart_import import clean_import_name
        cleaned, qty, indicator = clean_import_name('earcup_x2_mirror')
        self.assertEqual(cleaned, 'earcup')
        self.assertEqual(qty, 2)
        self.assertTrue(indicator)

    def test_mirror_counterpart_name(self):
        from .smart_import import mirror_counterpart_name
        self.assertEqual(mirror_counterpart_name('earcup (Left)'), 'earcup (Right)')
        self.assertEqual(mirror_counterpart_name('earcup (Right)'), 'earcup (Left)')
        self.assertEqual(mirror_counterpart_name('earcup (left)'), 'earcup (Right)')
        self.assertEqual(mirror_counterpart_name('earcup'), 'earcup (Left)')

    def test_mirror_pair_names(self):
        from .smart_import import mirror_pair_names
        # Unmarked: main becomes (Right), counterpart (Left)
        self.assertEqual(mirror_pair_names('earcup'), ('earcup (Right)', 'earcup (Left)'))
        # Marked: main keeps its name
        self.assertEqual(mirror_pair_names('earcup (Left)'), ('earcup (Left)', 'earcup (Right)'))
        self.assertEqual(mirror_pair_names('earcup (Right)'), ('earcup (Right)', 'earcup (Left)'))

    def test_resolve_manifest_quantity(self):
        from .smart_import import resolve_manifest_quantity
        # Explicit valid quantity wins
        self.assertEqual(resolve_manifest_quantity({'quantity': 2}), 2)
        # Missing quantity falls back to the STL file name
        self.assertEqual(
            resolve_manifest_quantity({'stl_file': 'stl_files/gear_x4.stl'}), 4
        )
        # Then to the part name
        self.assertEqual(resolve_manifest_quantity({'name': 'gear_3pcs'}), 3)
        # Nothing usable -> 1
        self.assertEqual(resolve_manifest_quantity({}), 1)
        # Invalid quantity (0, bool) falls back instead of being used
        self.assertEqual(
            resolve_manifest_quantity({'quantity': 0, 'stl_file': 'stl_files/gear_x2.stl'}), 2
        )
        self.assertEqual(resolve_manifest_quantity({'quantity': True}), 1)


class AddMultiplePartsMirrorTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser',
            password='testpass123'
        )
        self.client = Client()
        self.client.login(username='testuser', password='testpass123')
        self.project = Project.objects.create(
            name='Test Project',
            user=self.user
        )
        self.material = Material.objects.create(
            name='Test Material',
            type='PLA',
            description='Test Description',
            density=Decimal('1.25'),
            color='#FF0000',
            is_active=True,
            weight=Decimal('1000.00'),
            cost=Decimal('25.00')
        )

    def _post_parts(self, parts, file_names):
        from unittest.mock import patch
        files = {
            'parts': json.dumps(parts),
            'files': [
                SimpleUploadedFile(name, b'fake stl content', content_type='model/stl')
                for name in file_names
            ]
        }
        # Keep the test hermetic: no real background pipeline thread
        with patch('projects.volume.process_parts_background'):
            return self.client.post(
                reverse('projects:add_multiple_parts', args=[self.project.id]), files
            )

    def test_mirror_flag_creates_counterpart_sharing_file(self):
        parts = [{
            'name': 'earcup (Left)',
            'quantity': 1,
            'material': self.material.id,
            'color': '#FF0000',
            'mirror': True,
            'mirror_name': 'earcup (Right)'
        }]
        response = self._post_parts(parts, ['earcup (Left).stl'])
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data['status'], 'success')

        left = Part.objects.get(name='earcup (Left)')
        right = Part.objects.get(name='earcup (Right)')
        # Counterpart shares the same STL file, copies material/quantity
        self.assertEqual(right.stl_file, left.stl_file)
        self.assertEqual(right.material, left.material)
        self.assertEqual(right.quantity, left.quantity)

    def test_mirror_flag_without_names_uses_convention(self):
        # Unmarked name: main becomes (Right), counterpart (Left)
        parts = [{
            'name': 'earcup',
            'quantity': 2,
            'material': self.material.id,
            'color': '#FF0000',
            'mirror': True
        }]
        response = self._post_parts(parts, ['earcup.stl'])
        self.assertEqual(response.status_code, 200)

        main = Part.objects.get(name='earcup (Right)')
        counterpart = Part.objects.get(name='earcup (Left)')
        self.assertEqual(counterpart.stl_file, main.stl_file)
        # Quantity is split across the pair (total preserved: 2 -> 1+1)
        self.assertEqual(main.quantity, 1)
        self.assertEqual(counterpart.quantity, 1)

    def test_no_mirror_flag_creates_single_part(self):
        parts = [{
            'name': 'gear',
            'quantity': 1,
            'material': self.material.id,
            'color': '#FF0000'
        }]
        response = self._post_parts(parts, ['gear.stl'])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.project.parts.count(), 1)


class PartsTwoPhaseUploadTests(TestCase):
    """Two-phase Add Parts upload: parts_create (metadata) + parts_upload (file chunks)."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser',
            password='testpass123'
        )
        self.client = Client()
        self.client.login(username='testuser', password='testpass123')
        self.project = Project.objects.create(
            name='Test Project',
            user=self.user
        )
        self.material = Material.objects.create(
            name='Test Material',
            type='PLA',
            description='Test Description',
            density=Decimal('1.25'),
            color='#FF0000',
            is_active=True,
            weight=Decimal('1000.00'),
            cost=Decimal('25.00')
        )

    def _create_parts(self, parts):
        return self.client.post(
            reverse('projects:parts_create', args=[self.project.id]),
            {'parts': json.dumps(parts)}
        )

    def test_parts_create_creates_rows_and_task(self):
        parts = [
            {
                'name': 'gear',
                'quantity': 1,
                'material': self.material.id,
                'color': '#FF0000',
                'has_file': True,
            },
            {
                'name': 'earcup (Left)',
                'quantity': 2,
                'material': self.material.id,
                'color': '#FF0000',
                'mirror': True,
                'mirror_name': 'earcup (Right)',
                'has_file': True,
            },
        ]
        response = self._create_parts(parts)
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data['status'], 'success')
        self.assertTrue(data['task_id'])
        self.assertEqual(data['pipeline_total'], 3)

        rows = data['rows']
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(rows[0]['part_ids']), 1)
        self.assertEqual(len(rows[1]['part_ids']), 2)

        # Rows exist without files; mirror counterpart has no file yet
        main = Part.objects.get(id=rows[1]['part_ids'][0])
        mirror = Part.objects.get(id=rows[1]['part_ids'][1])
        self.assertFalse(main.stl_file)
        self.assertFalse(mirror.stl_file)
        self.assertEqual(mirror.name, 'earcup (Right)')

    def test_parts_create_requires_material(self):
        response = self._create_parts([
            {'name': 'gear', 'quantity': 1, 'has_file': True},
        ])
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.content)
        self.assertIn('Material is required', data['message'])
        self.assertEqual(Part.objects.filter(project=self.project).count(), 0)

    def test_mirror_splits_quantity_across_pair(self):
        # Odd quantity: main gets the extra (3 -> 2+1, total preserved)
        response = self._create_parts([
            {
                'name': 'earcup (Left)',
                'quantity': 3,
                'material': self.material.id,
                'color': '#FF0000',
                'mirror': True,
                'mirror_name': 'earcup (Right)',
                'has_file': True,
            },
        ])
        data = json.loads(response.content)
        part_ids = data['rows'][0]['part_ids']

        main = Part.objects.get(id=part_ids[0])
        mirror = Part.objects.get(id=part_ids[1])
        self.assertEqual(main.quantity, 2)
        self.assertEqual(mirror.quantity, 1)

    def test_mirror_single_piece_keeps_one_each(self):
        # A single piece can't be halved — both sides keep quantity 1
        response = self._create_parts([
            {
                'name': 'earcup',
                'quantity': 1,
                'material': self.material.id,
                'color': '#FF0000',
                'mirror': True,
                'has_file': True,
            },
        ])
        data = json.loads(response.content)
        part_ids = data['rows'][0]['part_ids']

        main = Part.objects.get(id=part_ids[0])
        mirror = Part.objects.get(id=part_ids[1])
        self.assertEqual(main.quantity, 1)
        self.assertEqual(mirror.quantity, 1)

    def test_parts_upload_attaches_files_and_links_mirrors(self):
        response = self._create_parts([
            {
                'name': 'earcup (Left)',
                'quantity': 1,
                'material': self.material.id,
                'color': '#FF0000',
                'mirror': True,
                'mirror_name': 'earcup (Right)',
                'has_file': True,
            },
        ])
        data = json.loads(response.content)
        task_id = data['task_id']
        part_ids = data['rows'][0]['part_ids']

        with patch('projects.volume.dispatch_pipeline'):
            upload = self.client.post(
                reverse('projects:parts_upload', args=[self.project.id]),
                {
                    'assignments': json.dumps([{'part_ids': part_ids}]),
                    'task_id': task_id,
                    'uploaded_rows': '1',
                    'file_rows': '1',
                    'files': [SimpleUploadedFile('earcup (Left).stl', b'fake stl content', content_type='model/stl')],
                }
            )
        self.assertEqual(upload.status_code, 200)
        up = json.loads(upload.content)
        self.assertEqual(up['status'], 'success')
        self.assertEqual(up['files_saved'], 1)
        self.assertTrue(up['done'])

        main = Part.objects.get(id=part_ids[0])
        mirror = Part.objects.get(id=part_ids[1])
        self.assertTrue(main.stl_file)
        self.assertEqual(mirror.stl_file, main.stl_file)

    def test_parts_upload_rejects_unknown_part_id(self):
        with patch('projects.volume.dispatch_pipeline'):
            response = self.client.post(
                reverse('projects:parts_upload', args=[self.project.id]),
                {
                    'assignments': json.dumps([{'part_ids': [999999]}]),
                    'task_id': '',
                    'uploaded_rows': '1',
                    'file_rows': '1',
                    'files': [SimpleUploadedFile('x.stl', b'fake stl content', content_type='model/stl')],
                }
            )
        self.assertEqual(response.status_code, 400)
        self.assertIn('Unknown part id', json.loads(response.content)['message'])

    def test_add_multiple_parts_too_many_files_returns_json_error(self):
        with override_settings(DATA_UPLOAD_MAX_NUMBER_FILES=2), \
                patch('projects.volume.process_parts_background'):
            response = self.client.post(
                reverse('projects:add_multiple_parts', args=[self.project.id]),
                {
                    'parts': json.dumps([
                        {'name': f'p{i}', 'quantity': 1, 'material': self.material.id}
                        for i in range(3)
                    ]),
                    'files': [
                        SimpleUploadedFile(f'p{i}.stl', b'fake stl content', content_type='model/stl')
                        for i in range(3)
                    ],
                }
            )
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.content)
        self.assertEqual(data['status'], 'error')
        self.assertIn('split it into smaller batches', data['message'])


class ProgressChunkedUploadTests(TestCase):
    def test_append_items_dedupes_by_part_id(self):
        from projects import progress

        tid = 'dedupe-test'
        progress.create_task(tid, 3)
        try:
            progress.append_items(tid, [(1, '/a.stl'), (2, '/b.stl')])
            # Re-sending the same chunk (resume after a network timeout)
            progress.append_items(tid, [(1, '/a.stl'), (3, '/c.stl')])

            items = progress.drain_pending(tid)
            self.assertEqual([i[0] for i in items], [1, 2, 3])
            task = progress.get_task(tid)
            self.assertEqual(task['total'], 3)
        finally:
            progress.cleanup_task(tid)

    def test_wait_uploads_done_returns_true_after_signal(self):
        from projects import progress

        tid = 'wait-test'
        progress.create_task(tid, 1)
        try:
            progress.set_uploads_done(tid)
            self.assertTrue(progress.wait_uploads_done(tid, timeout=5))
        finally:
            progress.cleanup_task(tid)


class ProjectDefaultMaterialTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser',
            password='testpass123'
        )
        self.client = Client()
        self.client.login(username='testuser', password='testpass123')
        self.material = Material.objects.create(
            name='Default PLA',
            type='PLA',
            description='',
            density=Decimal('1.25'),
            color='#FF0000',
            is_active=True,
            weight=Decimal('1000.00'),
            cost=Decimal('25.00')
        )
        self.other_material = Material.objects.create(
            name='Import ABS',
            type='ABS',
            description='',
            density=Decimal('1.05'),
            color='#00FF00',
            is_active=True,
            weight=Decimal('1000.00'),
            cost=Decimal('30.00')
        )
        self.project = Project.objects.create(
            name='Test Project',
            user=self.user
        )

    def test_project_form_saves_default_material(self):
        from .forms import ProjectForm
        form = ProjectForm(data={
            'name': 'New Project',
            'description': '',
            'designer': '',
            'default_material': self.material.id,
            'source': '',
            'tags': ''
        })
        self.assertTrue(form.is_valid())
        project = form.save(commit=False)
        project.user = self.user
        project.save()
        self.assertEqual(project.default_material, self.material)

    def test_part_form_preselects_default_and_requires_material(self):
        from .forms import PartForm
        self.project.default_material = self.material
        self.project.save()
        form = PartForm(project=self.project)
        self.assertEqual(form.initial['material'], self.material.id)
        self.assertTrue(form.fields['material'].required)
        invalid = PartForm(data={'name': 'P', 'quantity': 1, 'color': '#000'}, project=self.project)
        self.assertFalse(invalid.is_valid())
        self.assertIn('material', invalid.errors)

    def test_import_falls_back_to_default_material(self):
        manifest = {
            'metadata': {
                'name': 'Fallback Import',
                'description': '',
                'default_material_name': 'Default PLA'
            },
            'materials': [{
                'id': 1,
                'name': 'Import ABS',
                'type': 'ABS',
                'description': '',
                'density': 1.05,
                'color': '#00FF00',
                'is_active': True,
                'brand': '',
                'link': '',
                'cost': 30.00,
                'weight': 1000.00
            }],
            'groups': [],
            'parts': [
                {
                    'name': 'NoMat Part',
                    'quantity': 1,
                    'material_id': None,
                    'color': '#FF0000',
                    'completed': 0,
                    'group_id': None,
                    'volume': 10.0
                },
                {
                    'name': 'WithMat Part',
                    'quantity': 1,
                    'material_id': 1,
                    'color': '#FF0000',
                    'completed': 0,
                    'group_id': None,
                    'volume': 10.0
                }
            ],
            'purchased_parts': [],
            'instructions': [],
            'project_images': []
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            zip_path = os.path.join(temp_dir, 'test.zip')
            with zipfile.ZipFile(zip_path, 'w') as zf:
                zf.writestr('manifest.json', json.dumps(manifest))
            with open(zip_path, 'rb') as f:
                response = self.client.post(reverse('projects:import_project'), {
                    'zip_file': SimpleUploadedFile('test.zip', f.read())
                })
        self.assertEqual(response.status_code, 302)
        imported = Project.objects.get(name='Fallback Import')
        self.assertEqual(imported.default_material, self.material)
        self.assertEqual(Part.objects.get(name='NoMat Part').material, self.material)
        self.assertEqual(Part.objects.get(name='WithMat Part').material, self.other_material)

    def test_export_import_round_trip_preserves_default(self):
        self.project.name = 'Round Trip Source'
        self.project.default_material = self.material
        self.project.save()
        response = self.client.get(reverse('projects:export_project', args=[self.project.id]))
        self.assertEqual(response.status_code, 200)
        # Re-import the exported zip as-is
        response = self.client.post(reverse('projects:import_project'), {
            'zip_file': SimpleUploadedFile('export.zip', response.content)
        })
        self.assertEqual(response.status_code, 302)
        # Both source and import share the name — the import is the newest
        imported = Project.objects.filter(name='Round Trip Source').order_by('-id').first()
        self.assertEqual(imported.default_material, self.material)

    def test_add_multiple_parts_falls_back_to_default(self):
        from unittest.mock import patch
        self.project.default_material = self.material
        self.project.save()
        files = {
            'parts': json.dumps([{'name': 'gear', 'quantity': 1, 'color': '#FF0000'}]),
            'files': [SimpleUploadedFile('gear.stl', b'fake stl content', content_type='model/stl')]
        }
        with patch('projects.volume.process_parts_background'):
            response = self.client.post(
                reverse('projects:add_multiple_parts', args=[self.project.id]), files
            )
        self.assertEqual(response.status_code, 200)
        part = Part.objects.get(name='gear')
        self.assertEqual(part.material, self.material)

    def test_add_multiple_parts_requires_material_without_default(self):
        from unittest.mock import patch
        files = {
            'parts': json.dumps([{'name': 'gear', 'quantity': 1, 'color': '#FF0000'}]),
            'files': [SimpleUploadedFile('gear.stl', b'fake stl content', content_type='model/stl')]
        }
        with patch('projects.volume.process_parts_background'):
            response = self.client.post(
                reverse('projects:add_multiple_parts', args=[self.project.id]), files
            )
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.content)
        self.assertEqual(data['status'], 'error')


class Export3MFTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='exporter',
            password='testpass123'
        )
        self.client = Client()
        self.client.login(username='exporter', password='testpass123')
        self.project = Project.objects.create(name='Export Test', user=self.user)
        self.other_project = Project.objects.create(name='Other Project', user=self.user)

    def _stl_bytes(self, extents=(10, 20, 5)):
        import trimesh
        return trimesh.creation.box(extents=extents).export(file_type='stl')

    def _make_part(self, name, quantity=1, completed=0, with_file=True, project=None, parent=None):
        part = Part(
            project=project or self.project,
            name=name,
            quantity=quantity,
            completed=completed,
            parent=parent,
        )
        part._skip_volume = True
        part._skip_thumbnail = True
        if with_file:
            part.stl_file.save(f'{name}.stl', ContentFile(self._stl_bytes()), save=False)
        part.save()
        return part

    def _post_export(self, part_ids, mark_complete=False):
        return self.client.post(
            reverse('projects:export_3mf', args=[self.project.id]),
            data=json.dumps({'part_ids': part_ids, 'mark_complete': mark_complete}),
            content_type='application/json',
        )

    def _read_model(self, response):
        with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
            return zf.read('3D/3dmodel.model').decode('utf-8')

    def test_export_3mf_multiple_parts_with_quantities(self):
        part_a = self._make_part('part_a', quantity=3)
        part_b = self._make_part('part_b', quantity=2, completed=1)

        response = self._post_export([part_a.id, part_b.id])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response['Content-Type'],
            'application/vnd.ms-package.3dmanufacturing-3dmodel+xml'
        )
        self.assertIn('export-test_parts.3mf', response['Content-Disposition'])

        model = self._read_model(response)
        # part_a: 3 remaining, part_b: 1 remaining -> 4 items, 2 unique objects
        self.assertEqual(model.count('<item '), 4)
        self.assertEqual(model.count('<object id='), 2)
        self.assertIn('unit="millimeter"', model)

    def test_export_3mf_zip_structure(self):
        import xml.etree.ElementTree as ET
        part = self._make_part('part_a', quantity=1)
        response = self._post_export([part.id])
        self.assertEqual(response.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
            names = set(zf.namelist())
            model_xml = zf.read('3D/3dmodel.model')
        self.assertIn('[Content_Types].xml', names)
        self.assertIn('_rels/.rels', names)
        self.assertIn('3D/3dmodel.model', names)
        # Model XML must be well-formed and carry a valid 16-value transform
        root = ET.fromstring(model_xml)
        items = root.findall('.//{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}item')
        self.assertEqual(len(items), 1)
        values = [v.strip() for v in items[0].attrib['transform'].split(',')]
        self.assertEqual(len(values), 16)
        for value in values:
            float(value)

    def test_export_3mf_mark_complete(self):
        part_a = self._make_part('part_a', quantity=3, completed=1)
        part_b = self._make_part('part_b', quantity=2)

        response = self._post_export([part_a.id, part_b.id], mark_complete=True)
        self.assertEqual(response.status_code, 200)
        part_a.refresh_from_db()
        part_b.refresh_from_db()
        self.assertEqual(part_a.completed, 3)
        self.assertEqual(part_b.completed, 2)

    def test_export_3mf_without_mark_complete_keeps_progress(self):
        part = self._make_part('part_a', quantity=3, completed=1)
        response = self._post_export([part.id], mark_complete=False)
        self.assertEqual(response.status_code, 200)
        part.refresh_from_db()
        self.assertEqual(part.completed, 1)

    def test_export_3mf_expands_parent_with_subparts(self):
        parent = self._make_part('assembly', quantity=4)
        child_a = self._make_part('child_a', quantity=4, parent=parent)
        child_b = self._make_part('child_b', quantity=4, parent=parent)

        response = self._post_export([parent.id])
        self.assertEqual(response.status_code, 200)
        model = self._read_model(response)
        # Parent expands to its two children: 2 x 4 copies
        self.assertEqual(model.count('<item '), 8)
        self.assertEqual(model.count('<object id='), 2)

    def test_export_3mf_parent_plus_children_no_double_count(self):
        parent = self._make_part('assembly', quantity=4)
        child_a = self._make_part('child_a', quantity=4, parent=parent)
        child_b = self._make_part('child_b', quantity=4, parent=parent)

        response = self._post_export([parent.id, child_a.id, child_b.id])
        self.assertEqual(response.status_code, 200)
        model = self._read_model(response)
        self.assertEqual(model.count('<item '), 8)

    def test_export_3mf_all_complete_returns_error(self):
        part = self._make_part('part_a', quantity=2, completed=2)
        response = self._post_export([part.id])
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.content)
        self.assertIn('No unprinted copies', data['message'])

    def test_export_3mf_missing_file_returns_error(self):
        part = self._make_part('part_a', quantity=1, with_file=False)
        response = self._post_export([part.id])
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.content)
        self.assertIn('part_a', data['message'])

    def test_export_3mf_file_deleted_from_disk_returns_error(self):
        from django.core.files.storage import default_storage
        part = self._make_part('part_a', quantity=1)
        stored_name = part.stl_file.name
        default_storage.delete(stored_name)
        response = self._post_export([part.id])
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.content)
        self.assertIn('Could not load geometry', data['message'])
        self.assertIn('part_a', data['message'])

    def test_export_3mf_rejects_parts_from_other_project(self):
        foreign = self._make_part('foreign', quantity=1, project=self.other_project)
        response = self._post_export([foreign.id])
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.content)
        self.assertIn('not found in this project', data['message'])

    def test_export_3mf_empty_selection_returns_error(self):
        response = self._post_export([])
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.content)
        self.assertEqual(data['status'], 'error')

    def test_export_3mf_requires_auth(self):
        part = self._make_part('part_a', quantity=1)
        self.client.logout()
        response = self.client.post(
            reverse('projects:export_3mf', args=[self.project.id]),
            data=json.dumps({'part_ids': [part.id]}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 302)


class ApplySmartParsingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='parser',
            password='testpass123'
        )
        self.client = Client()
        self.client.login(username='parser', password='testpass123')
        self.project = Project.objects.create(name='Legacy Project', user=self.user)
        self._created_files = []
        self.material = Material.objects.create(
            name='Test Material',
            type='PLA',
            description='',
            density=Decimal('1.25'),
            color='#FF0000',
            is_active=True,
            weight=Decimal('1000.00'),
            cost=Decimal('25.00')
        )

    def _stl_bytes(self):
        import trimesh
        return trimesh.creation.box(extents=(10, 20, 5)).export(file_type='stl')

    def _make_part(self, name, quantity=1, completed=0, with_file=True, file_name=None, material=None):
        part = Part(
            project=self.project,
            name=name,
            quantity=quantity,
            completed=completed,
            material=material,
        )
        part._skip_volume = True
        part._skip_thumbnail = True
        if with_file:
            part.stl_file.save(file_name or f'{name}.stl', ContentFile(self._stl_bytes()), save=False)
            self._created_files.append(part.stl_file.name)
        part.save()
        return part

    def tearDown(self):
        from django.core.files.storage import default_storage
        for name in self._created_files:
            try:
                default_storage.delete(name)
            except Exception:
                pass

    def _post(self):
        response = self.client.post(reverse('projects:apply_smart_parsing', args=[self.project.id]))
        self.assertEqual(response.status_code, 200)
        return json.loads(response.content)

    def test_quantity_parsed_from_name_and_cleaned(self):
        part = self._make_part('bracket_x3')
        data = self._post()
        part.refresh_from_db()
        self.assertEqual(part.quantity, 3)
        self.assertEqual(part.name, 'bracket')
        self.assertIn('bracket (1\u21923)', data['quantities_updated'])

    def test_quantity_parsed_from_filename(self):
        part = self._make_part('bracket', file_name='bracket_2x.stl')
        data = self._post()
        part.refresh_from_db()
        self.assertEqual(part.quantity, 2)
        self.assertEqual(part.name, 'bracket')
        self.assertIn('bracket (1\u21922)', data['quantities_updated'])

    def test_manual_quantity_preserved(self):
        part = self._make_part('gear_x4', quantity=5)
        data = self._post()
        part.refresh_from_db()
        self.assertEqual(part.quantity, 5)
        self.assertEqual(part.name, 'gear_x4')
        self.assertEqual(data['quantities_updated'], [])

    def test_completed_progress_unchanged(self):
        part = self._make_part('bracket_x3', completed=1)
        self._post()
        part.refresh_from_db()
        self.assertEqual(part.quantity, 3)
        self.assertEqual(part.completed, 1)

    def test_mirror_counterpart_created_sharing_file_and_stats(self):
        part = self._make_part('earcup (Left)', material=self.material)
        data = self._post()
        self.assertIn('earcup (Right)', data['mirrors_created'])
        counterpart = Part.objects.get(name='earcup (Right)')
        self.assertEqual(counterpart.stl_file, part.stl_file)
        self.assertEqual(counterpart.material, self.material)
        self.assertEqual(counterpart.quantity, 1)

    def test_mirror_inherits_quantity_corrected_by_pass_one(self):
        part = self._make_part('earcup (Left)', file_name='earcup_2x.stl')
        self._post()
        part.refresh_from_db()
        self.assertEqual(part.quantity, 2)
        counterpart = Part.objects.get(name='earcup (Right)')
        self.assertEqual(counterpart.quantity, 2)

    def test_mirror_not_created_when_counterpart_exists(self):
        self._make_part('earcup (Left)')
        self._make_part('earcup (Right)')
        data = self._post()
        self.assertEqual(data['mirrors_created'], [])
        self.assertEqual(self.project.parts.count(), 2)

    def test_mirror_idempotent_on_rerun(self):
        self._make_part('earcup (Left)')
        self._post()
        data = self._post()
        self.assertEqual(data['mirrors_created'], [])
        self.assertEqual(data['quantities_updated'], [])
        self.assertEqual(self.project.parts.count(), 2)

    def test_mirror_skipped_for_parent_with_subparts(self):
        parent = self._make_part('assembly (Left)')
        Part.objects.create(
            project=self.project,
            name='assembly (Left) - Part 1',
            quantity=1,
            parent=parent,
        )
        data = self._post()
        self.assertEqual(data['mirrors_created'], [])

    def test_mirror_skipped_without_file(self):
        self._make_part('earcup (Left)', with_file=False)
        data = self._post()
        self.assertEqual(data['mirrors_created'], [])

    def test_nothing_to_do_returns_success(self):
        self._make_part('plain part')
        data = self._post()
        self.assertEqual(data['status'], 'success')
        self.assertEqual(data['quantities_updated'], [])
        self.assertEqual(data['mirrors_created'], [])
        self.assertIn('No changes needed', data['message'])

    def test_requires_auth(self):
        self.client.logout()
        response = self.client.post(reverse('projects:apply_smart_parsing', args=[self.project.id]))
        self.assertEqual(response.status_code, 302)


class AutoNameParsingSettingTests(TestCase):
    """User setting to toggle filename-based auto name parsing on Add Parts."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='settingsuser',
            password='testpass123'
        )
        self.client = Client()
        self.client.login(username='settingsuser', password='testpass123')
        self.project = Project.objects.create(name='Settings Project', user=self.user)

    def _save_general(self, extra):
        data = {
            'settings_type': 'general',
            'default_material': '',
            'unit_system': 'imperial',
        }
        data.update(extra)
        response = self.client.post(reverse('projects:settings'), data)
        self.assertEqual(response.status_code, 302)

    def _general_data(self):
        from .models import UserSettings
        instance = UserSettings.objects.get(user=self.user, settings_type='general')
        return json.loads(instance.settings_data)

    def test_add_parts_defaults_to_enabled(self):
        response = self.client.get(reverse('projects:add_multiple_parts', args=[self.project.id]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['auto_name_parsing'])

    def test_toggle_on_then_off_is_persisted(self):
        self._save_general({'auto_name_parsing': 'on'})
        self.assertIs(self._general_data()['auto_name_parsing'], True)

        self._save_general({})
        self.assertIs(self._general_data()['auto_name_parsing'], False)

    def test_disabled_setting_reaches_add_parts_context(self):
        self._save_general({})
        response = self.client.get(reverse('projects:add_multiple_parts', args=[self.project.id]))
        self.assertFalse(response.context['auto_name_parsing'])

    def test_settings_page_reflects_toggle_state(self):
        response = self.client.get(reverse('projects:settings'))
        self.assertContains(response, 'name="auto_name_parsing" checked')

        self._save_general({})
        response = self.client.get(reverse('projects:settings'))
        self.assertNotIn(b'name="auto_name_parsing" checked', response.content)


class ShellInfillCostTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='shelluser', password='password123')
        self.material = Material.objects.create(
            name='Test PLA',
            type='PLA',
            density=Decimal('1.25'),
            cost=Decimal('25.00'),
            weight=Decimal('1000.00')
        )
        self.project = Project.objects.create(name='Shell Project', user=self.user)
        self.part = Part.objects.create(
            name='50mm Cube',
            project=self.project,
            material=self.material,
            volume=125000.0,
            surface_area=15000.0
        )

    def test_effective_volume_calculation(self):
        # 50mm cube: volume=125,000 mm³, area=15,000 mm²
        # shell_vol = min(125000, 15000 * 1.2) = 18,000 mm³
        # core_vol = 125000 - 18000 = 107,000 mm³
        # infill_vol = 107000 * 0.15 = 16,050 mm³
        # printed_vol = 18000 + 16050 = 34,050 mm³
        self.assertAlmostEqual(self.part.effective_volume, 34050.0, places=1)

    def test_material_cost_uses_shell_infill(self):
        # Printed volume: 34.05 cm³ * 1.25 g/cm³ = 42.5625 g
        # Cost at $25/kg: 42.5625 * $0.025 = $1.0640625
        # (Raw solid cost would have been $3.90625)
        self.assertAlmostEqual(self.part.material_cost, 1.0640625, places=5)

    def test_thin_wall_part_capped_at_total_volume(self):
        # Thin sheet where area * 1.2 > volume
        thin_part = Part.objects.create(
            name='Thin Sheet',
            project=self.project,
            material=self.material,
            volume=10000.0,
            surface_area=20000.0
        )
        self.assertEqual(thin_part.effective_volume, 10000.0)

    def test_slicer_weight_takes_precedence_over_geometry(self):
        # When slicer filament_weight_g is present, use it directly
        self.part.filament_weight_g = Decimal('20.00')
        self.part.save()
        # 20g * $0.025/g = $0.50
        self.assertAlmostEqual(self.part.material_cost, 0.50, places=4)

    def test_compute_mesh_stats_stl(self):
        import tempfile
        import trimesh
        from projects.volume import compute_mesh_stats

        box = trimesh.creation.box(extents=[10, 10, 10])
        with tempfile.NamedTemporaryFile(suffix='.stl', delete=False) as f:
            temp_path = f.name
            box.export(temp_path)

        try:
            stats = compute_mesh_stats(temp_path)
            self.assertIsNotNone(stats)
            vol, area = stats
            self.assertAlmostEqual(vol, 1000.0, places=1)
            self.assertAlmostEqual(area, 600.0, places=1)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_part_details_api_includes_surface_area_and_filament_weight(self):
        self.client.login(username='shelluser', password='password123')
        self.part.filament_weight_g = Decimal('25.50')
        self.part.save()
        response = self.client.get(reverse('projects:part_details', args=[self.part.id]))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['surface_area'], 15000.0)
        self.assertEqual(data['filament_weight_g'], 25.50)

class CollectionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='collectionuser', password='testpass123')
        self.client = Client()
        self.client.login(username='collectionuser', password='testpass123')
        self.project_a = Project.objects.create(name='Project A', user=self.user)
        self.project_b = Project.objects.create(name='Project B', user=self.user)

    def post_json(self, url, data):
        return self.client.post(url, data=json.dumps(data), content_type='application/json')

    def test_project_list_view_includes_collections(self):
        collection = Collection.objects.create(name='My Collection')
        collection.projects.add(self.project_a)
        response = self.client.get(reverse('projects:project_list'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('collections', response.context)
        self.assertEqual(list(response.context['collections']), [collection])

    def test_create_collection(self):
        response = self.post_json(reverse('projects:collection_create'), {'name': 'Living Room', 'description': 'Furniture'})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'success')
        collection = Collection.objects.get(name='Living Room')
        self.assertEqual(collection.description, 'Furniture')
        self.assertEqual(data['id'], collection.id)

    def test_create_collection_requires_name(self):
        response = self.post_json(reverse('projects:collection_create'), {'name': '   '})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['status'], 'error')

    def test_create_collection_with_projects(self):
        response = self.post_json(reverse('projects:collection_create'), {
            'name': 'With Projects',
            'project_ids': [self.project_a.id, self.project_b.id],
        })
        self.assertEqual(response.json()['status'], 'success')
        collection = Collection.objects.get(name='With Projects')
        self.assertEqual(collection.projects.count(), 2)

    def test_create_collection_rejects_unknown_project_ids(self):
        response = self.post_json(reverse('projects:collection_create'), {
            'name': 'Bad',
            'project_ids': [self.project_a.id, 999999],
        })
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Collection.objects.filter(name='Bad').exists())

    def test_update_collection(self):
        collection = Collection.objects.create(name='Old Name')
        response = self.post_json(reverse('projects:collection_update', args=[collection.id]), {
            'name': 'New Name',
            'description': 'Updated',
            'cover_project_id': self.project_a.id,
        })
        self.assertEqual(response.json()['status'], 'success')
        collection.refresh_from_db()
        self.assertEqual(collection.name, 'New Name')
        self.assertEqual(collection.description, 'Updated')
        self.assertEqual(collection.cover_project_id, self.project_a.id)

    def test_update_collection_clears_cover(self):
        collection = Collection.objects.create(name='C', cover_project=self.project_a)
        response = self.post_json(reverse('projects:collection_update', args=[collection.id]), {
            'name': 'C',
            'cover_project_id': None,
        })
        self.assertEqual(response.json()['status'], 'success')
        collection.refresh_from_db()
        self.assertIsNone(collection.cover_project_id)

    def test_update_collection_requires_name(self):
        collection = Collection.objects.create(name='C')
        response = self.post_json(reverse('projects:collection_update', args=[collection.id]), {'name': ''})
        self.assertEqual(response.status_code, 400)

    def test_delete_collection_keeps_projects(self):
        collection = Collection.objects.create(name='Doomed')
        collection.projects.add(self.project_a)
        response = self.post_json(reverse('projects:collection_delete', args=[collection.id]), {})
        self.assertEqual(response.json()['status'], 'success')
        self.assertFalse(Collection.objects.filter(id=collection.id).exists())
        self.assertTrue(Project.objects.filter(id=self.project_a.id).exists())

    def test_add_projects_to_collection(self):
        collection = Collection.objects.create(name='Target')
        response = self.post_json(reverse('projects:collection_add_projects', args=[collection.id]), {
            'project_ids': [self.project_a.id],
        })
        self.assertEqual(response.json()['status'], 'success')
        self.assertEqual(list(collection.projects.all()), [self.project_a])

    def test_add_projects_is_idempotent(self):
        collection = Collection.objects.create(name='Target')
        url = reverse('projects:collection_add_projects', args=[collection.id])
        self.post_json(url, {'project_ids': [self.project_a.id]})
        self.post_json(url, {'project_ids': [self.project_a.id]})
        self.assertEqual(collection.projects.count(), 1)

    def test_add_projects_preserves_unselected_members(self):
        collection = Collection.objects.create(name='Target')
        collection.projects.add(self.project_b)
        response = self.post_json(reverse('projects:collection_add_projects', args=[collection.id]), {
            'project_ids': [self.project_a.id],
        })
        self.assertEqual(response.json()['status'], 'success')
        self.assertEqual(collection.projects.count(), 2)

    def test_add_projects_requires_ids(self):
        collection = Collection.objects.create(name='Target')
        response = self.post_json(reverse('projects:collection_add_projects', args=[collection.id]), {})
        self.assertEqual(response.status_code, 400)

    def test_remove_project_from_collection(self):
        collection = Collection.objects.create(name='Target')
        collection.projects.add(self.project_a, self.project_b)
        response = self.post_json(reverse('projects:collection_remove_project', args=[collection.id]), {
            'project_id': self.project_a.id,
        })
        self.assertEqual(response.json()['status'], 'success')
        self.assertEqual(list(collection.projects.all()), [self.project_b])

    def test_collection_endpoints_404_for_missing_collection(self):
        client = Client(raise_request_exception=False)
        client.login(username='collectionuser', password='testpass123')
        response = client.post(
            reverse('projects:collection_add_projects', args=[999999]),
            data=json.dumps({'project_ids': [self.project_a.id]}),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 404)

    def test_cover_image_fallback_chain(self):
        collection = Collection.objects.create(name='Art')
        self.assertIsNone(collection.cover_image)

        ProjectImage.objects.create(project=self.project_a, image=SimpleUploadedFile('a.jpg', b''))
        collection.projects.add(self.project_a)
        self.assertEqual(collection.cover_image.project_id, self.project_a.id)

        collection.cover_project = self.project_b
        collection.save()
        # Cover project has no image yet → falls back to first member
        self.assertEqual(collection.cover_image.project_id, self.project_a.id)

        ProjectImage.objects.create(project=self.project_b, image=SimpleUploadedFile('b.jpg', b''))
        self.assertEqual(collection.cover_image.project_id, self.project_b.id)


class ImportProgressTests(TestCase):
    """Async (AJAX) import: task hand-off, worker completion, progress store fields.

    The worker is patched to run inline (like dispatch_pipeline in the upload
    tests) so no background thread opens a second DB connection against the
    test transaction.
    """

    def setUp(self):
        self.user = User.objects.create_user(username='importuser', password='testpass123')
        self.client = Client()
        self.client.login(username='importuser', password='testpass123')

    def _make_zip(self, manifest=None, entries=None):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as zf:
            if manifest is not None:
                zf.writestr('manifest.json', json.dumps(manifest))
            for name, content in (entries or {}).items():
                zf.writestr(name, content)
        return buf.getvalue()

    def _manifest(self, parts=None):
        return {
            'metadata': {'name': 'Async Import', 'description': 'desc'},
            'materials': [{
                'id': 1, 'name': 'Test Material', 'type': 'PLA', 'description': '',
                'density': 1.25, 'color': '#FF0000', 'is_active': True,
                'brand': '', 'link': '', 'cost': 25.00, 'weight': 1000.00
            }],
            'groups': [{'id': 1, 'name': 'Test Group'}],
            'parts': parts or [],
            'purchased_parts': [],
            'instructions': [],
            'project_images': []
        }

    def _post_ajax(self, zip_bytes):
        """AJAX POST with the import worker run inline (no background thread)."""
        from projects import views as project_views

        def run_inline(task_id, zip_path, user):
            project_views._run_import_task(task_id, zip_path, user)

        with patch('projects.views._start_import_worker', side_effect=run_inline):
            return self.client.post(
                reverse('projects:import_project'),
                {'zip_file': SimpleUploadedFile('test.zip', zip_bytes)},
                HTTP_X_REQUESTED_WITH='XMLHttpRequest'
            )

    def test_import_page_renders_with_progress_modal(self):
        response = self.client.get(reverse('projects:import_project'))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('importProgressModal', html)
        self.assertIn('/api/upload-progress/', html)

    def test_ajax_import_returns_task_and_completes(self):
        response = self._post_ajax(self._make_zip(self._manifest(parts=[
            {'name': 'Gear', 'quantity': 1, 'material_id': 1, 'color': '#FF0000',
             'completed': 0, 'group_id': 1, 'volume': 10.0}
        ])))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'success')

        from projects import progress
        task = progress.get_task(data['task_id'])
        self.assertTrue(task['done'])
        self.assertIsNone(task['error'])
        project = Project.objects.get(name='Async Import')
        self.assertIn(str(project.id), task['redirect_url'])
        self.assertEqual(Part.objects.filter(project=project, name='Gear').count(), 1)

    def test_ajax_import_missing_manifest_rejected(self):
        response = self._post_ajax(self._make_zip(None, entries={'readme.txt': 'hello'}))
        self.assertEqual(response.status_code, 400)
        self.assertIn('manifest', response.json()['message'])
        self.assertFalse(Project.objects.exists())

    def test_ajax_import_invalid_zip_rejected(self):
        response = self._post_ajax(b'this is not a zip file')
        self.assertEqual(response.status_code, 400)

    def test_ajax_import_bad_manifest_json_rejected(self):
        response = self._post_ajax(self._make_zip(None, entries={'manifest.json': '{not json'}))
        self.assertEqual(response.status_code, 400)

    def test_import_failure_reports_error(self):
        # Manifest parses but lacks required keys -> worker fails after hand-off
        response = self._post_ajax(self._make_zip({'metadata': {}}))
        self.assertEqual(response.status_code, 200)
        from projects import progress
        task = progress.get_task(response.json()['task_id'])
        self.assertTrue(task['done'])
        self.assertTrue(task['error'])
        self.assertFalse(Project.objects.exists())

    def test_progress_finish_and_percent_override(self):
        from projects import progress
        tid = 'import-test-1'
        progress.create_task(tid, 1)
        progress.update_task(tid, phase='parts', percent=55, status='Importing part 2 of 4')
        task = progress.get_task(tid)
        self.assertEqual(task['percent'], 55)
        self.assertIsNone(task['error'])

        progress.finish(tid, True, redirect_url='/projects/9/')
        task = progress.get_task(tid)
        self.assertTrue(task['done'])
        self.assertEqual(task['percent'], 100)
        self.assertEqual(task['redirect_url'], '/projects/9/')

        tid2 = 'import-test-2'
        progress.create_task(tid2, 1)
        progress.update_task(tid2, phase='parts', percent=40)
        progress.finish(tid2, False, error='boom')
        task2 = progress.get_task(tid2)
        self.assertTrue(task2['done'])
        self.assertEqual(task2['percent'], 40)
        self.assertEqual(task2['error'], 'boom')

        # Legacy upload pipeline phases are unaffected
        tid3 = 'import-test-3'
        progress.create_task(tid3, 10)
        progress.update_task(tid3, phase='volumes', completed=5)
        self.assertEqual(progress.get_task(tid3)['percent'], 67 + int(5 / 10 * 33))

        progress.cleanup_task(tid)
        progress.cleanup_task(tid2)
        progress.cleanup_task(tid3)


class OrientationTests(SimpleTestCase):
    def _box(self, lx, ly, lz):
        x, y, z = lx / 2, ly / 2, lz / 2
        v = np.array([[-x, -y, -z], [x, -y, -z], [x, y, -z], [-x, y, -z],
                      [-x, -y, z], [x, -y, z], [x, y, z], [-x, y, z]], dtype=float)
        faces = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4),
                 (3, 6, 2), (3, 7, 6), (0, 4, 7), (0, 7, 3), (1, 2, 6), (1, 6, 5)]
        return v[np.array(faces)]

    def _cone(self, r, h, n=32):
        theta = np.linspace(0, 2 * np.pi, n, endpoint=False)
        ring = np.column_stack([r * np.cos(theta), r * np.sin(theta), np.full(n, h)])
        apex = np.array([0.0, 0.0, 0.0])
        center = np.array([0.0, 0.0, h])
        tris = []
        for i in range(n):
            j = (i + 1) % n
            tris.append([apex, ring[j], ring[i]])
            tris.append([center, ring[i], ring[j]])
        return np.array(tris)

    def test_rotation_is_valid(self):
        from projects.orient import best_orientation
        R, info = best_orientation(self._box(20, 20, 20))
        self.assertTrue(np.allclose(R @ R.T, np.eye(3), atol=1e-9))
        self.assertAlmostEqual(float(np.linalg.det(R)), 1.0, places=9)
        self.assertGreater(info['n_candidates'], 10)

    def test_plate_lays_flat(self):
        from projects.orient import best_orientation
        # Big faces vertical as modeled (standing on a 40x2 edge)
        plate = self._box(40, 2, 30)
        R, info = best_orientation(plate)
        rotated = (R @ plate.reshape(-1, 3).T).T
        z_range = rotated[:, 2].max() - rotated[:, 2].min()
        self.assertLess(z_range, 2.5)

    def test_plate_already_flat_stays(self):
        from projects.orient import best_orientation
        plate = self._box(40, 30, 2)
        R, info = best_orientation(plate)
        self.assertTrue(np.allclose(R, np.eye(3), atol=1e-9))

    def test_cone_flips_base_down(self):
        from projects.orient import best_orientation
        r, h = 10.0, 6.0
        cone = self._cone(r, h)  # apex down as modeled: sides overhang past 45 deg
        total_area = sum(
            0.5 * np.linalg.norm(np.cross(t[1] - t[0], t[2] - t[0])) for t in cone
        )
        smooth = math.pi * r * r + math.pi * r * math.sqrt(r * r + h * h)
        # Faceted cone area is slightly under the smooth-cone value
        self.assertGreater(total_area, 0.98 * smooth)
        self.assertLess(total_area, smooth)

        R, info = best_orientation(cone)
        pts = (R @ cone.reshape(-1, 3).T).T
        zmin, zmax = pts[:, 2].min(), pts[:, 2].max()
        # Vertex mean of a cone sits 5/6 h from the apex; base-down puts it in
        # the lower half of the bounding box. Apex-down (no flip) would fail this.
        self.assertLess(pts[:, 2].mean() - zmin, (zmax - zmin) / 2)


class MachineSliceProfileTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='muser', password='pass123')

    def _machine(self, **kwargs):
        return Machine.objects.create(
            user=self.user, name='Test Printer',
            print_volume_x=220, print_volume_y=220, print_volume_z=250, **kwargs
        )

    def test_defaults_applied_when_empty(self):
        m = self._machine()
        profile = m.get_slice_profile()
        self.assertEqual(profile, Machine.SLICE_PROFILE_DEFAULTS)

    def test_partial_profile_merges_over_defaults(self):
        m = self._machine(slice_profile={'layer_height': 0.12, 'infill_pct': 20})
        profile = m.get_slice_profile()
        self.assertEqual(profile['layer_height'], 0.12)
        self.assertEqual(profile['infill_pct'], 20)
        # Untouched keys keep defaults
        self.assertEqual(profile['shells'], Machine.SLICE_PROFILE_DEFAULTS['shells'])
        self.assertEqual(profile['support_enabled'], True)

    def test_none_profile_treated_as_empty(self):
        m = self._machine()
        m.slice_profile = None
        profile = m.get_slice_profile()
        self.assertEqual(profile, Machine.SLICE_PROFILE_DEFAULTS)


class MachineSliceProfileFormTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='slicefmuser', password='pass123')
        self.client = Client()
        self.client.login(username='slicefmuser', password='pass123')

    def _machine_post(self, action, machine_id=None, **overrides):
        # support_enabled 'on' mirrors the browser: the form renders it checked by default
        data = {
            'machine_action': action,
            'name': 'Test Printer', 'maker': 'Creality', 'model': 'Ender 3',
            'technology': 'FDM',
            'print_volume_x': '220', 'print_volume_y': '220', 'print_volume_z': '250',
            'ip_address': '', 'mqtt_access_code': '', 'notes': '',
            # slice profile defaults
            'layer_height': '0.2', 'shells': '2', 'infill_pct': '15',
            'support_enabled': 'on', 'support_angle': '45', 'support_density': '100',
            'nozzle_diameter': '0.4', 'filament_diameter': '1.75',
        }
        if machine_id:
            data['machine_id'] = str(machine_id)
        for k, v in overrides.items():
            if v is True:
                data[k] = 'on'
            elif v is False:
                data.pop(k, None)  # unchecked checkbox posts nothing
            else:
                data[k] = v
        return self.client.post(reverse('projects:settings'), data)

    def test_add_machine_stores_only_non_default_values(self):
        response = self._machine_post('add', layer_height='0.28', infill_pct='30', brim=True)
        self.assertEqual(response.status_code, 302)
        m = Machine.objects.get(user=self.user)
        self.assertEqual(m.slice_profile, {'layer_height': 0.28, 'infill_pct': 30, 'brim': True})
        profile = m.get_slice_profile()
        self.assertEqual(profile['shells'], 2)  # default kept via merge

    def test_add_machine_all_defaults_stores_empty(self):
        response = self._machine_post('add', support_enabled=True)
        self.assertEqual(response.status_code, 302)
        m = Machine.objects.get(user=self.user)
        self.assertEqual(m.slice_profile, {})

    def test_edit_reverting_to_default_removes_key(self):
        m = Machine.objects.create(
            user=self.user, name='Test Printer',
            print_volume_x=220, print_volume_y=220, print_volume_z=250,
            slice_profile={'layer_height': 0.28}
        )
        response = self._machine_post('edit', machine_id=m.id)  # back to default 0.2
        self.assertEqual(response.status_code, 302)
        m.refresh_from_db()
        self.assertEqual(m.slice_profile, {})

    def test_edit_form_initial_reflects_stored_profile(self):
        Machine.objects.create(
            user=self.user, name='Test Printer',
            print_volume_x=220, print_volume_y=220, print_volume_z=250,
            slice_profile={'layer_height': 0.32, 'brim': True}
        )
        m = Machine.objects.get(user=self.user)
        response = self.client.get(reverse('projects:settings'), {'edit_machine': m.id})
        self.assertContains(response, 'value="0.32"')
        self.assertRegex(response.content, rb'name="brim"[^>]*checked')

    def test_settings_page_renders_slice_fields(self):
        response = self.client.get(reverse('projects:settings'))
        self.assertEqual(response.status_code, 200)
        for field_id in ('id_layer_height', 'id_shells', 'id_infill_pct', 'id_support_enabled',
                         'id_support_angle', 'id_support_density', 'id_brim',
                         'id_nozzle_diameter', 'id_filament_diameter'):
            self.assertContains(response, f'id="{field_id}"')


class SliceEstimateTests(TestCase):
    """slice_estimate_part: background sidecar slicing (run inline here), heuristic
    fallback, advisory storage, and the Kiri tier of material cost."""

    def setUp(self):
        self.user = User.objects.create_user(username='slicerm', password='pass123')
        self.client = Client()
        self.client.login(username='slicerm', password='pass123')
        self.project = Project.objects.create(name='Slice Test', user=self.user)
        self.material = Material.objects.create(
            name='PLA Black', type='PLA', density=Decimal('1.24'),
            cost=Decimal('25.00'), weight=Decimal('1000.00'),
        )
        self.machine = Machine.objects.create(
            user=self.user, name='Ender 3',
            print_volume_x=220, print_volume_y=220, print_volume_z=250,
        )
        self.part = self._make_part('cube', 'slice_cube.stl', (20, 20, 20),
                                    volume=8000.0, surface_area=2400.0)

    def tearDown(self):
        for part in Part.objects.filter(project=self.project).exclude(stl_file=''):
            part.stl_file.delete(save=False)

    def _make_part(self, name, filename, extents, **fields):
        import trimesh
        stl_bytes = trimesh.creation.box(extents=extents).export(file_type='stl')
        part = Part(project=self.project, name=name, material=self.material, **fields)
        part._skip_volume = True
        part._skip_thumbnail = True
        part.stl_file.save(filename, ContentFile(stl_bytes), save=False)
        part.save()
        return part

    @staticmethod
    def _sidecar_ok(filament_mm=1000.0, time_s=3600):
        resp = Mock(status_code=200)
        resp.json.return_value = {
            'ok': True, 'filament_mm': filament_mm, 'time_s': time_s,
            'breakdown_mm': {'shell': 600.0, 'infill': 300.0, 'support': 100.0},
        }
        return resp

    def _post(self, part=None, machine=None):
        part = part or self.part
        return self.client.post(
            reverse('projects:slice_estimate_part', args=[self.project.id, part.id]),
            data=json.dumps({'machine_id': (machine or self.machine).id}),
            content_type='application/json',
        )

    def _estimate(self, part=None, machine=None, **post_kwargs):
        """POST with the sidecar enabled, running the background worker inline.
        Returns (response, final task dict)."""
        from projects import progress, views
        with override_settings(SLICER_SERVICE_URL='http://slicer:8090'), \
                patch('projects.views._start_slice_worker', side_effect=views._run_slice_task), \
                patch('projects.slicer_estimate.requests.post', **post_kwargs) as post:
            response = self._post(part, machine)
        self.post_mock = post
        task_id = response.json().get('task_id')
        return response, progress.get_task(task_id) if task_id else None

    def _slicer_call(self):
        """The /slice call among patched requests.post calls (Blender thumbnail
        signal calls also hit the shared module-level post)."""
        for c in self.post_mock.call_args_list:
            if str(c.args[0]).endswith('/slice'):
                return c
        return None

    def test_slicer_estimate_never_overwrites_exact_slice_data(self):
        # Weight/time from a real claimed slice must survive a Kiri estimate
        self.part.filament_weight_g = Decimal('42.00')
        self.part.print_time_seconds = 7200
        self.part.save()
        response, task = self._estimate(return_value=self._sidecar_ok())
        self.assertEqual(response.status_code, 200)
        self.assertTrue(task['done'])
        self.assertIsNone(task['error'])
        self.part.refresh_from_db()
        self.assertEqual(self.part.filament_weight_g, Decimal('42.00'))
        self.assertEqual(self.part.print_time_seconds, 7200)
        self.assertEqual(self.part.slice_estimate['source'], 'slicer')
        # exact weight still wins the cost: 42g x $0.025/g
        self.assertAlmostEqual(self.part.material_cost, 1.05, places=4)
        # sidecar got the auto-oriented part with the machine profile mapped
        payload = self._slicer_call().kwargs['json']
        self.assertEqual(payload['device']['bedWidth'], 220)
        self.assertEqual(payload['process']['sliceHeight'], 0.2)
        self.assertEqual(payload['process']['sliceSupportType'], 'automatic')
        self.assertAlmostEqual(payload['process']['sliceFillSparse'], 0.15, places=6)

    def test_slicer_estimate_drives_cost_without_exact_weight(self):
        self._estimate(return_value=self._sidecar_ok())
        self.part.refresh_from_db()
        # 1000mm x (pi/4 * 1.75^2) mm^2 / 1000 x 1.24 g/cm^3 = 2.98 g -> $0.0745
        self.assertAlmostEqual(self.part.slicer_weight_g, 2.98, places=2)
        self.assertAlmostEqual(self.part.material_cost, 2.98 * 0.025, places=4)
        self.part.completed = 2
        self.part.save()
        self.assertAlmostEqual(float(self.material.total_used), 5.96, places=2)

        data = self.client.get(reverse('projects:part_details', args=[self.part.id])).json()
        est = data['slice_estimate']
        self.assertEqual(est['source'], 'slicer')
        self.assertAlmostEqual(est['grams'], 2.98, places=2)
        self.assertAlmostEqual(est['cost'], 0.0745, places=4)
        self.assertEqual(est['time_s'], 3600)
        self.assertEqual(est['machine_id'], self.machine.id)
        self.assertAlmostEqual(est['breakdown_g']['shell'], 1.79, places=2)
        self.assertAlmostEqual(est['breakdown_g']['support'], 0.30, places=2)
        self.assertIsNone(est['warning'])

    def test_slicer_grams_follow_current_material_density(self):
        self._estimate(return_value=self._sidecar_ok())
        self.material.density = Decimal('2.48')
        self.material.save()
        part = Part.objects.get(id=self.part.id)
        self.assertAlmostEqual(part.slicer_weight_g, 5.97, places=2)  # 1000mm x 2.405mm^2 x 2.48

    def test_replaced_file_invalidates_slicer_estimate(self):
        self._estimate(return_value=self._sidecar_ok())
        part = Part.objects.get(id=self.part.id)
        part.stl_file.save('slice_cube_v2.stl', ContentFile(b'solid x\nendsolid x\n'), save=False)
        part._skip_volume = True
        part._skip_thumbnail = True
        part.volume, part.surface_area = 8000.0, 2400.0
        part.save()
        self.assertIsNone(part.slicer_weight_g)
        # cost falls through to the shell + infill heuristic
        expected_g = part.effective_volume / 1000 * 1.24
        self.assertAlmostEqual(part.material_cost, expected_g * 0.025, places=4)
        est = self.client.get(reverse('projects:part_details', args=[part.id])).json()['slice_estimate']
        self.assertIsNone(est['grams'])
        self.assertIn('changed', est['warning'])

    def test_no_slicer_url_returns_heuristic_inline(self):
        with override_settings(SLICER_SERVICE_URL=''), \
                patch('projects.views._start_slice_worker') as worker:
            response = self._post()
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertNotIn('task_id', data)
        self.assertEqual(data['estimate']['source'], 'estimate')
        self.assertIsNone(data['estimate']['time_s'])
        self.assertIsNotNone(data['estimate']['grams'])
        worker.assert_not_called()
        self.part.refresh_from_db()
        self.assertIsNone(self.part.filament_weight_g)
        self.assertEqual(self.part.slice_estimate['source'], 'estimate')

    def test_sidecar_down_saves_heuristic_with_warning(self):
        import requests as _requests
        response, task = self._estimate(side_effect=_requests.ConnectionError('refused'))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(task['done'])
        self.assertIsNone(task['error'])
        self.part.refresh_from_db()
        self.assertEqual(self.part.slice_estimate['source'], 'estimate')
        self.assertIn('Slicer unavailable', self.part.slice_estimate['warning'])
        self.assertIsNone(self.part.filament_weight_g)

    def test_part_too_big_reports_error_and_saves_nothing(self):
        tiny = Machine.objects.create(
            user=self.user, name='Tiny',
            print_volume_x=10, print_volume_y=10, print_volume_z=10,
        )
        response, task = self._estimate(machine=tiny)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(task['done'])
        self.assertIn('does not fit', task['error'])
        self.assertIsNone(self._slicer_call())
        self.part.refresh_from_db()
        self.assertEqual(self.part.slice_estimate, {})

    def test_long_part_fits_after_quarter_turn(self):
        # 200mm bar only fits a 50 x 250 x 15 bed lying along Y
        bar = self._make_part('bar', 'slice_bar.stl', (200, 20, 10))
        narrow = Machine.objects.create(
            user=self.user, name='Narrow',
            print_volume_x=50, print_volume_y=250, print_volume_z=15,
        )
        _, task = self._estimate(part=bar, machine=narrow, return_value=self._sidecar_ok())
        self.assertIsNone(task['error'])
        self.assertIsNotNone(self._slicer_call())
        bar.refresh_from_db()
        self.assertEqual(bar.slice_estimate['source'], 'slicer')

    def test_missing_machine_and_file_errors(self):
        response = self.client.post(
            reverse('projects:slice_estimate_part', args=[self.project.id, self.part.id]),
            data=json.dumps({}), content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)

        fileless = Part.objects.create(project=self.project, name='nofile')
        response = self._post(part=fileless)
        self.assertEqual(response.status_code, 400)

    def test_foreign_machine_rejected(self):
        other = User.objects.create_user(username='otheru', password='pass123')
        foreign = Machine.objects.create(
            user=other, name='Foreign',
            print_volume_x=220, print_volume_y=220, print_volume_z=250,
        )
        response = self._post(machine=foreign)
        self.assertEqual(response.status_code, 404)


class ProjectLibraryTests(TestCase):
    def setUp(self):
        from .models import Tag
        self.user = User.objects.create_user(username='libuser', password='testpass123')
        self.client = Client()
        self.client.login(username='libuser', password='testpass123')
        self.designer = Designer.objects.create(name='Zed Designs')
        self.tag_a = Tag.objects.create(name='alpha')
        self.tag_b = Tag.objects.create(name='alphabet')
        self.done = Project.objects.create(name='banana', user=self.user)
        self.partial = Project.objects.create(name='Apple', user=self.user, designer=self.designer)
        self.fresh = Project.objects.create(name='cherry', user=self.user)
        Part.objects.create(project=self.done, name='d1', quantity=2, completed=2)
        Part.objects.create(project=self.partial, name='p1', quantity=2, completed=1)
        Part.objects.create(project=self.fresh, name='f1', quantity=2, completed=0)
        self.done.tags.add(self.tag_a, self.tag_b)
        self.url = reverse('projects:project_list')

    def names(self, response):
        return [p.name for p in response.context['projects']]

    def test_status_filters(self):
        for status, project in (('complete', self.done), ('in_progress', self.partial), ('not_started', self.fresh)):
            response = self.client.get(self.url, {'status': status})
            self.assertEqual(self.names(response), [project.name], status)

    def test_only_leaf_parts_count(self):
        project = Project.objects.create(name='Nested', user=self.user)
        parent = Part.objects.create(project=project, name='parent', quantity=5, completed=0)
        Part.objects.create(project=project, name='leaf', quantity=1, completed=1, parent=parent)
        response = self.client.get(self.url, {'status': 'complete'})
        self.assertIn('Nested', self.names(response))
        nested = next(p for p in response.context['projects'] if p.name == 'Nested')
        self.assertEqual(nested.parts_total, 1)
        self.assertEqual(nested.progress_pct, 100)

    def test_collection_filter(self):
        collection = Collection.objects.create(name='Fruit')
        collection.projects.add(self.done)
        response = self.client.get(self.url, {'collection': collection.id})
        self.assertEqual(self.names(response), ['banana'])
        self.assertEqual(response.context['active_collection'], collection)

    def test_missing_collection_redirects(self):
        response = self.client.get(self.url, {'collection': 99999})
        self.assertRedirects(response, self.url)

    def test_tag_filter_and_q_no_duplicates(self):
        response = self.client.get(self.url, {'tag': 'alpha'})
        self.assertEqual(self.names(response), ['banana'])
        response = self.client.get(self.url, {'q': 'alpha'})
        self.assertEqual(self.names(response), ['banana'])

    def test_q_matches_designer(self):
        response = self.client.get(self.url, {'q': 'zed'})
        self.assertEqual(self.names(response), ['Apple'])

    def test_sort_name_case_insensitive(self):
        response = self.client.get(self.url, {'sort': 'name'})
        self.assertEqual(self.names(response), ['Apple', 'banana', 'cherry'])

    def test_status_counts_ignore_filters(self):
        response = self.client.get(self.url, {'tag': 'alpha'})
        self.assertEqual(response.context['status_counts'],
                         {'all': 3, 'not_started': 1, 'in_progress': 1, 'complete': 1})

    def test_view_mode_cookie(self):
        response = self.client.get(self.url, {'view': 'list'})
        self.assertEqual(response.context['view_mode'], 'list')
        self.assertEqual(response.cookies['project_view'].value, 'list')
        response = self.client.get(self.url)
        self.assertEqual(response.context['view_mode'], 'list')


class DesignerDeleteTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='designeruser', password='pw')
        self.client.login(username='designeruser', password='pw')
        self.designer = Designer.objects.create(name='Deletable')

    def test_delete_unused_designer(self):
        response = self.client.post(reverse('projects:designer_delete', args=[self.designer.id]))
        self.assertRedirects(response, reverse('projects:designer_index'))
        self.assertFalse(Designer.objects.filter(id=self.designer.id).exists())

    def test_delete_refused_while_projects_reference_designer(self):
        project = Project.objects.create(name='Attributed', user=self.user, designer=self.designer)
        response = self.client.post(reverse('projects:designer_delete', args=[self.designer.id]), follow=True)
        self.assertRedirects(response, reverse('projects:designer_detail', args=[self.designer.id]))
        self.assertTrue(Designer.objects.filter(id=self.designer.id).exists())
        project.refresh_from_db()
        self.assertEqual(project.designer, self.designer)
        self.assertIn('still assigned to 1 project.', str(list(response.context['messages'])[0]))

    def test_delete_requires_post(self):
        response = self.client.get(reverse('projects:designer_delete', args=[self.designer.id]))
        self.assertEqual(response.status_code, 405)
        self.assertTrue(Designer.objects.filter(id=self.designer.id).exists())
