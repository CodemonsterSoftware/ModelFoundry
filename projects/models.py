from django.db import models
from django.utils import timezone
from django.db.models import Sum
from django.contrib.auth.models import User
import os
import numpy as np

from decimal import Decimal, InvalidOperation
from django.conf import settings
from imagekit.models import ImageSpecField
from imagekit.processors import ResizeToFill, ResizeToFit

class Designer(models.Model):
    name = models.CharField(max_length=100)
    logo = models.ImageField(upload_to='designer_logos/', null=True, blank=True)
    logo_thumbnail = ImageSpecField(source='logo',
                                    processors=[ResizeToFit(200, 200)],
                                    format='PNG')
    mmf_url = models.URLField(max_length=200, blank=True, null=True)
    patreon_url = models.URLField(max_length=200, blank=True, null=True)
    cults3d_url = models.URLField(max_length=200, blank=True, null=True)
    website_url = models.URLField(max_length=200, blank=True, null=True)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['name']

class Tag(models.Model):
    name = models.CharField(max_length=50, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['name']

class Project(models.Model):
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='projects')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    designer = models.ForeignKey(Designer, on_delete=models.SET_NULL, null=True, blank=True, related_name='projects')
    tags = models.ManyToManyField(Tag, blank=True, related_name='projects')
    source = models.URLField(max_length=500, blank=True, help_text="Link to where the project files were sourced from")
    default_material = models.ForeignKey(
        'Material', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='default_for_projects',
        help_text="Material pre-selected for new parts and used as a fallback on import"
    )

    @property
    def total_parts(self):
        return self.parts.filter(sub_parts__isnull=True).aggregate(total=Sum('quantity'))['total'] or 0

    @property
    def progress_percentage(self):
        total = self.total_parts
        if total == 0:
            return 0
        completed = self.parts.filter(sub_parts__isnull=True).aggregate(total_completed=Sum('completed'))['total_completed'] or 0
        return int((completed / total) * 100)

    @property
    def material_counts(self):
        counts = {}
        for part in self.parts.filter(sub_parts__isnull=True):
            if part.material:
                counts[part.material] = counts.get(part.material, 0) + part.quantity
        return counts

    @property
    def printed_parts_cost(self):
        total = Decimal('0')
        for part in self.parts.filter(sub_parts__isnull=True):
            if part.material_cost:
                total += Decimal(str(part.material_cost)) * part.quantity
        return float(total)

    @property
    def purchased_parts_cost(self):
        total = Decimal('0')
        for purchased_part in self.purchased_parts.all():
            total += purchased_part.price * purchased_part.quantity
        return float(total)

    @property
    def total_cost(self):
        """
        Calculate the total cost of the project including both printed and purchased parts.
        Returns the total cost as a float.
        """
        return self.printed_parts_cost + self.purchased_parts_cost

    def __str__(self):
        return self.name

class Collection(models.Model):
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    cover_project = models.ForeignKey(Project, on_delete=models.SET_NULL, null=True, blank=True, related_name='cover_for_collections')
    projects = models.ManyToManyField(Project, blank=True, related_name='collections')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

    @property
    def cover_image(self):
        """First image of the cover project, else first image of the first member."""
        if self.cover_project:
            first = self.cover_project.images.first()
            if first:
                return first
        first_member = self.projects.order_by('created_at').first()
        if first_member:
            return first_member.images.first()
        return None

    class Meta:
        ordering = ['name']

class Group(models.Model):
    name = models.CharField(max_length=200)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='groups')

    def __str__(self):
        return self.name

class Material(models.Model):
    MATERIAL_TYPES = [
        ('PLA', 'PLA'),
        ('PLA+', 'PLA+'),
        ('PLA-CF', 'PLA Carbon Fiber'),
        ('PLA-GF', 'PLA Glass Fiber'),
        ('PLA-Silk', 'PLA Silk'),
        ('ABS', 'ABS'),
        ('ABS-GF', 'ABS Glass Fiber'),
        ('ABS-CF', 'ABS Carbon Fiber'),
        ('PETG', 'PETG'),
        ('PETG-CF', 'PETG Carbon Fiber'),
        ('PETG-GF', 'PETG Glass Fiber'),
        ('PCTG', 'PCTG'),
        ('TPU', 'TPU'),
        ('TPE', 'TPE'),
        ('Nylon', 'Nylon (PA)'),
        ('PA-CF', 'PA Carbon Fiber'),
        ('PA-GF', 'PA Glass Fiber'),
        ('PC', 'Polycarbonate (PC)'),
        ('PC-CF', 'PC Carbon Fiber'),
        ('ASA', 'ASA'),
        ('HIPS', 'HIPS'),
        ('PVA', 'PVA'),
        ('BVOH', 'BVOH'),
        ('PP', 'Polypropylene (PP)'),
        ('PEEK', 'PEEK'),
        ('PEKK', 'PEKK'),
        ('PEI', 'PEI (Ultem)'),
        ('POM', 'POM (Delrin)'),
        ('Resin', 'Standard Resin'),
        ('Resin-Tough', 'Tough Resin'),
        ('Resin-Flex', 'Flexible Resin'),
        ('Resin-Cast', 'Castable Resin'),
        ('Resin-Water', 'Water Washable Resin'),
        ('Other', 'Other'),
    ]
    
    name = models.CharField(max_length=100, unique=True)
    type = models.CharField(max_length=20, choices=MATERIAL_TYPES, default='PLA')
    description = models.TextField(blank=True)
    density = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True, help_text='Density in grams per cubic centimeter (g/cm³)')
    color = models.CharField(max_length=50, blank=True, help_text='Default color for this material (optional)')
    is_active = models.BooleanField(default=True)
    brand = models.CharField(max_length=100, blank=True, help_text='Manufacturer or brand name')
    link = models.URLField(max_length=200, blank=True, help_text='Link to purchase or product page')
    cost = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, help_text='Total cost of the material', default=25.00)
    weight = models.DecimalField(max_digits=10, decimal_places=2, help_text='Total weight in grams', default=1000.00)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def cost_per_kg(self):
        if self.weight and self.cost:
            # Convert weight from grams to kilograms and calculate cost per kg
            return (self.cost / self.weight) * 1000
        return None

    @property
    def total_used(self):
        """Calculate total material used in grams based on completed parts and density"""
        total_mass = Decimal('0')
        
        # Get all completed parts that use this material (exclude parent parts that have sub-parts)
        completed_parts = Part.objects.filter(material=self, completed__gt=0, sub_parts__isnull=True)
        
        for part in completed_parts:
            if part.filament_weight_g is not None and part.filament_weight_g > 0:
                total_mass += Decimal(str(part.filament_weight_g)) * part.completed
            elif part.volume:
                vol_mm3 = Decimal(str(part.volume))
                if part.surface_area:
                    area_mm2 = Decimal(str(part.surface_area))
                    shell_vol = min(vol_mm3, area_mm2 * Part.DEFAULT_WALL_THICKNESS_MM)
                    core_vol = max(Decimal('0'), vol_mm3 - shell_vol)
                    printed_vol_mm3 = shell_vol + (core_vol * Part.DEFAULT_INFILL_RATIO)
                    volume_cm3 = printed_vol_mm3 / Decimal('1000')
                else:
                    volume_cm3 = vol_mm3 / Decimal('1000')
                if self.density:
                    total_mass += volume_cm3 * self.density * part.completed
        
        return total_mass

    def __str__(self):
        return f"{self.name} ({self.get_type_display()})"

    class Meta:
        ordering = ['name']

class Part(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='parts')
    name = models.CharField(max_length=200)
    quantity = models.IntegerField(default=1)
    material = models.ForeignKey(Material, on_delete=models.SET_NULL, null=True, blank=True, related_name='parts')
    material_name = models.CharField(max_length=100, blank=True)  # Keep for backward compatibility
    color = models.CharField(max_length=50, blank=True)
    completed = models.IntegerField(default=0)
    group = models.ForeignKey(Group, on_delete=models.SET_NULL, null=True, blank=True, related_name='parts')
    parent = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True, related_name='sub_parts')
    stl_file = models.FileField(upload_to='stl_files/', blank=True)
    thumbnail = models.ImageField(upload_to='thumbnails/', blank=True)
    thumbnail_small = ImageSpecField(source='thumbnail',
                                     processors=[ResizeToFill(150, 150)],
                                     format='JPEG',
                                     options={'quality': 85})
    thumbnail_medium = ImageSpecField(source='thumbnail',
                                      processors=[ResizeToFill(350, 350)],
                                      format='JPEG',
                                      options={'quality': 85})
    volume = models.FloatField(null=True, blank=True, help_text="Volume in cubic millimeters")
    surface_area = models.FloatField(null=True, blank=True, help_text="Surface area in square millimeters")
    
    # Slicer Integration Fields
    print_time_seconds = models.IntegerField(null=True, blank=True, help_text="Total print time for one copy in seconds")
    filament_weight_g = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, help_text="Filament weight for one copy in grams")
    assigned_slice_filename = models.CharField(max_length=255, blank=True, help_text="Filename of the assigned slice for exact MQTT tracking")
    
    PRINT_STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('printing', 'Printing'),
        ('completed', 'Completed'),
    ]
    print_status = models.CharField(max_length=20, choices=PRINT_STATUS_CHOICES, default='pending')

    # Standard FDM slicing defaults for geometry-based material estimation
    DEFAULT_WALL_THICKNESS_MM = Decimal('1.2')  # ~3 perimeters at 0.4mm nozzle / 6 top-bottom layers
    DEFAULT_INFILL_RATIO = Decimal('0.15')      # 15% standard infill

    def __str__(self):
        return self.name

    @property
    def effective_volume(self):
        """
        Calculate estimated printed volume in mm³ using the Shell + Infill model:
        - Shell volume = min(Volume, Surface Area * 1.2 mm wall thickness)
        - Core volume = max(0, Volume - Shell volume)
        - Printed volume = Shell volume + (Core volume * 15% infill)
        """
        if not self.volume:
            return None
        if self.surface_area:
            vol_mm3 = Decimal(str(self.volume))
            area_mm2 = Decimal(str(self.surface_area))
            shell_vol = min(vol_mm3, area_mm2 * self.DEFAULT_WALL_THICKNESS_MM)
            core_vol = max(Decimal('0'), vol_mm3 - shell_vol)
            return float(shell_vol + (core_vol * self.DEFAULT_INFILL_RATIO))
        return self.volume

    @property
    def material_cost(self):
        """
        Calculate the material cost for this part.
        
        Priority:
        1. Exact sliced weight (filament_weight_g) if available.
        2. Shell + Infill geometry approximation if surface_area is available:
           - Shell volume: surface_area × 1.2mm (capped at total volume)
           - Core volume: (total volume - shell volume) × 15% infill
           - Mass: printed volume (cm³) × density (g/cm³)
        3. Fallback: Solid volume × density.
        """
        if not self.material:
            return None
            
        try:
            if self.filament_weight_g is not None and self.filament_weight_g > 0:
                mass_g = Decimal(str(self.filament_weight_g))
            elif self.volume:
                vol_mm3 = Decimal(str(self.volume))
                if self.surface_area:
                    area_mm2 = Decimal(str(self.surface_area))
                    shell_vol = min(vol_mm3, area_mm2 * self.DEFAULT_WALL_THICKNESS_MM)
                    core_vol = max(Decimal('0'), vol_mm3 - shell_vol)
                    printed_vol_mm3 = shell_vol + (core_vol * self.DEFAULT_INFILL_RATIO)
                    volume_cm3 = printed_vol_mm3 / Decimal('1000')
                else:
                    volume_cm3 = vol_mm3 / Decimal('1000')
                mass_g = volume_cm3 * self.material.density
            else:
                return None
            
            # Convert mass to kg (1 kg = 1000 g) and calculate cost
            mass_kg = mass_g / Decimal('1000')
            cost = mass_kg * self.material.cost_per_kg
            
            return float(cost)  # Convert back to float for JSON serialization
        except (TypeError, ValueError, InvalidOperation):
            return None

    def calculate_volume(self):
        """Calculate the volume and surface area of the 3D file in cubic millimeters.
        
        Uses numpy-stl for STL files (fast vectorized path) and falls
        back to trimesh for other formats (OBJ, etc.).
        Also populates self.surface_area when available.
        """
        if not self.stl_file:
            print(f"No 3D file for part {self.name}")
            return None
            
        try:
            file_path = self.stl_file.path
            print(f"Calculating volume for {self.name} using file: {file_path}")
            
            if not os.path.exists(file_path):
                print(f"File not found at path: {file_path}")
                return None

            from projects.volume import compute_mesh_stats
            stats = compute_mesh_stats(file_path)
            
            if stats is not None:
                volume_mm3, surface_area_mm2 = stats
                self.surface_area = surface_area_mm2
                print(f"Volume calculated for {self.name}: {volume_mm3} mm³, area: {surface_area_mm2} mm²")
                return volume_mm3
            return None
            
        except Exception as e:
            print(f"Error calculating volume for part {self.name}: {str(e)}")
            return None

    def save(self, *args, **kwargs):
        # Calculate volume before saving if there's an STL file
        # _skip_volume flag allows bulk uploads to defer volume calc
        # to a background ProcessPoolExecutor for parallelism
        skip_volume = getattr(self, '_skip_volume', False)
        if not skip_volume and self.stl_file and (not self.volume or not self.surface_area or self._state.adding):
            print(f"Calculating volume for part {self.name}")
            vol = self.calculate_volume()
            if vol is not None:
                self.volume = vol
        super().save(*args, **kwargs)

class PurchasedPart(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('ordered', 'Ordered'),
        ('shipped', 'Shipped'),
        ('delivered', 'Delivered'),
    ]

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='purchased_parts')
    name = models.CharField(max_length=200)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    quantity = models.IntegerField(default=1)
    link = models.URLField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')

    def __str__(self):
        return self.name

class ProjectImage(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='images')
    image = models.ImageField(upload_to='project_images/')
    image_thumbnail = ImageSpecField(source='image',
                                     processors=[ResizeToFill(800, 600)],
                                     format='JPEG',
                                     options={'quality': 85})
    order = models.IntegerField(default=0)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['order']

    def __str__(self):
        return f"Image for {self.project.name}"

class Instructions(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='instructions')
    file = models.FileField(upload_to='instructions/', help_text='Instruction file (image, PDF, or DOCX)')
    description = models.TextField(blank=True, help_text='Description of this instruction step')
    order = models.PositiveIntegerField(default=0, help_text='Order in which this instruction should be displayed')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['order']
        verbose_name_plural = 'Instructions'

    def __str__(self):
        return f"Step {self.order} - {self.project.name}"
    
    @property
    def file_type(self):
        """Determine the file type based on the file extension"""
        if not self.file:
            return None
        filename = self.file.name.lower()
        if filename.endswith(('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp')):
            return 'image'
        elif filename.endswith(('.mp4', '.webm', '.ogg', '.mov')):
            return 'video'
        elif filename.endswith('.pdf'):
            return 'pdf'
        elif filename.endswith(('.doc', '.docx')):
            return 'docx'
        return 'other'
    
    @property
    def is_image(self):
        """Check if the file is an image"""
        return self.file_type == 'image'

    @property
    def is_video(self):
        """Check if the file is a video"""
        return self.file_type == 'video'

class UserSettings(models.Model):
    """Model to store user settings in JSON format"""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    settings_type = models.CharField(max_length=50, choices=[
        ('general', 'General Settings'),
        ('appearance', 'Appearance Settings'),
        ('api', 'API Security Settings'),
        ('machines', 'Machine Settings'),
        ('system', 'System Settings')
    ])
    settings_data = models.TextField(blank=True, null=True)  # Store JSON data
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        unique_together = ('user', 'settings_type')
        verbose_name = 'User Setting'
        verbose_name_plural = 'User Settings'
    
    def __str__(self):
        return f"{self.user.username}'s {self.get_settings_type_display()}"

class Machine(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='machines')
    name = models.CharField(max_length=100)
    maker = models.CharField(max_length=100, blank=True, null=True)
    model = models.CharField(max_length=100, blank=True, null=True)
    technology_choices = [
        ('FDM', 'FDM (Fused Deposition Modeling)'),
        ('SLA', 'SLA (Stereolithography)'),
        ('SLS', 'SLS (Selective Laser Sintering)'),
        ('DLP', 'DLP (Digital Light Processing)'),
        ('MJF', 'MJF (Multi Jet Fusion)'),
        ('Other', 'Other Technology'),
    ]
    technology = models.CharField(max_length=10, choices=technology_choices, default='FDM')
    print_volume_x = models.PositiveIntegerField(help_text="Print volume X in mm")
    print_volume_y = models.PositiveIntegerField(help_text="Print volume Y in mm")
    print_volume_z = models.PositiveIntegerField(help_text="Print volume Z in mm")
    
    # MQTT Sync Fields
    ip_address = models.CharField(max_length=45, blank=True, null=True, help_text="IP address of the printer on the local network")
    mqtt_access_code = models.CharField(max_length=100, blank=True, null=True, help_text="Access code for MQTT authentication")
    last_seen = models.DateTimeField(null=True, blank=True, help_text="Last time this machine was seen online")
    
    @property
    def is_online(self):
        from django.utils import timezone
        import datetime
        if not self.last_seen:
            return False
        return timezone.now() - self.last_seen < datetime.timedelta(minutes=5)
    
    notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.maker} {self.model} ({self.name})"

    class Meta:
        ordering = ['name']

class UnclaimedSlice(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('claimed', 'Claimed'),
        ('dismissed', 'Dismissed'),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='unclaimed_slices')
    filename = models.CharField(max_length=255)
    print_time_seconds = models.IntegerField(null=True, blank=True)
    filament_weight_g = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    filament_type = models.CharField(max_length=100, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def formatted_print_time(self):
        if not self.print_time_seconds:
            return None
        hours = self.print_time_seconds // 3600
        minutes = (self.print_time_seconds % 3600) // 60
        seconds = self.print_time_seconds % 60
        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    def __str__(self):
        return f"{self.filename} ({self.get_status_display()})"

    class Meta:
        ordering = ['-created_at']

class PrintHistory(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='print_history')
    part = models.ForeignKey(Part, on_delete=models.SET_NULL, null=True, blank=True)
    machine = models.ForeignKey(Machine, on_delete=models.SET_NULL, null=True, blank=True, related_name='print_history')
    filename = models.CharField(max_length=255, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, default='completed')

    def __str__(self):
        return f"{self.filename or self.part.name if self.part else 'Unknown'} - {self.status}"

    class Meta:
        ordering = ['-timestamp']
        verbose_name_plural = 'Print Histories'
