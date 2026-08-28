from django.test import TestCase, SimpleTestCase, Client, override_settings
from django.urls import reverse
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from .models import Project, Part, Group, PurchasedPart, Material, Designer, Instructions, ProjectImage
import io
import json
import os
import tempfile
import zipfile
from decimal import Decimal
from django.core.files import File
from django.core.files.base import ContentFile
from django.template.loader import render_to_string
from unittest.mock import patch

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
