from django import forms
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Submit, Row, Column, Field, ButtonHolder, Div
from .models import Project, Part, Group, PurchasedPart, ProjectImage, Designer, Material, Tag, UserSettings, Machine

class MultipleFileInput(forms.ClearableFileInput):
    def __init__(self, attrs=None):
        super().__init__(attrs)
        if attrs is None:
            attrs = {}
        attrs['multiple'] = True
        self.attrs = attrs

    def value_from_datadict(self, data, files, name):
        if hasattr(files, 'getlist'):
            return files.getlist(name)
        return None

class TagsInput(forms.TextInput):
    template_name = 'projects/widgets/tags_input.html'

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        context['widget']['attrs'].update({
            'class': 'form-control',
            'data-role': 'tagsinput',
            'placeholder': 'Add tags...'
        })
        return context

class ProjectForm(forms.ModelForm):
    tags = forms.CharField(
        required=False,
        widget=TagsInput()
    )

    class Meta:
        model = Project
        fields = ['name', 'description', 'designer', 'default_material', 'tags', 'source']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 4}),
            'source': forms.URLInput(attrs={'placeholder': 'https://example.com/project-source'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.layout = Layout(
            'name',
            'description',
            'designer',
            'default_material',
            'source',
            Div(
                'tags',
                css_class='form-group'
            )
        )
        self.fields['designer'].queryset = Designer.objects.all().order_by('name')
        self.fields['default_material'].queryset = Material.objects.filter(is_active=True).order_by('name')
        if self.instance.pk:
            self.initial['tags'] = ', '.join(tag.name for tag in self.instance.tags.all())

    def clean_tags(self):
        tags = self.cleaned_data.get('tags', '')
        return [tag.strip() for tag in tags.split(',') if tag.strip()]

    def save(self, commit=True):
        instance = super().save(commit=False)  # Don't commit yet to handle m2m
        if commit:
            instance.save()
            
            # Handle tags
            tags_string = self.cleaned_data.get('tags', '')
            if isinstance(tags_string, str):
                tag_names = [name.strip().lower() for name in tags_string.split(',') if name.strip()]
            else:
                tag_names = [tag.strip().lower() for tag in tags_string if tag.strip()]
            
            # Clear existing tags
            instance.tags.clear()
            
            # Create or get tags and add them to the instance
            for tag_name in tag_names:
                if tag_name:  # Only process non-empty tags
                    tag, _ = Tag.objects.get_or_create(name=tag_name)
                    instance.tags.add(tag)
                
        return instance

class PartForm(forms.ModelForm):
    class Meta:
        model = Part
        fields = ['name', 'quantity', 'material', 'color', 'group', 'stl_file', 'thumbnail']

    def __init__(self, *args, project=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['material'].required = True
        if project:
            self.fields['group'].queryset = project.groups.all()
            # Pre-select the project's default material for new parts
            if not self.instance.pk and project.default_material_id:
                self.initial['material'] = project.default_material_id

class GroupForm(forms.ModelForm):
    class Meta:
        model = Group
        fields = ['name']

class PurchasedPartForm(forms.ModelForm):
    class Meta:
        model = PurchasedPart
        fields = ['name', 'price', 'quantity', 'link', 'status']

class ProjectImageForm(forms.ModelForm):
    class Meta:
        model = ProjectImage
        fields = ['image']

class DesignerForm(forms.ModelForm):
    class Meta:
        model = Designer
        fields = ['name', 'logo', 'mmf_url', 'patreon_url', 'cults3d_url', 'website_url']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'logo': forms.FileInput(attrs={'class': 'form-control'}),
            'mmf_url': forms.URLInput(attrs={'class': 'form-control'}),
            'patreon_url': forms.URLInput(attrs={'class': 'form-control'}),
            'cults3d_url': forms.URLInput(attrs={'class': 'form-control'}),
            'website_url': forms.URLInput(attrs={'class': 'form-control'}),
        }

class MaterialForm(forms.ModelForm):
    class Meta:
        model = Material
        fields = ['name', 'type', 'description', 'density', 'color', 'is_active', 'brand', 'link', 'cost', 'weight']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 3}),
            'density': forms.NumberInput(attrs={'step': '0.001'}),
            'cost': forms.NumberInput(attrs={'step': '0.01'}),
            'weight': forms.NumberInput(attrs={'step': '0.01'}),
            'color': forms.TextInput(attrs={'type': 'color', 'class': 'form-control form-control-color'}),
        }
        help_texts = {
            'name': 'Enter a unique name for this material',
            'description': 'Describe the material and its properties',
            'density': 'Density in grams per cubic centimeter (g/cm³) (optional)',
            'color': 'Default color for this material (optional)',
            'is_active': 'Uncheck to hide this material from selection',
            'brand': 'Manufacturer or brand name',
            'link': 'Link to purchase or product page',
            'cost': 'Total cost of the material (optional)',
            'weight': 'Total weight in grams'
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.layout = Layout(
            Row(
                Column('name', css_class='col-md-6'),
                Column('brand', css_class='col-md-6'),
            ),
            'description',
            Row(
                Column('density', css_class='col-md-6'),
                Column('color', css_class='col-md-6'),
            ),
            Row(
                Column('cost', css_class='col-md-6'),
                Column('weight', css_class='col-md-6'),
            ),
            'link',
            'is_active',
        )
        
        # Add dark mode styling to help text
        for field_name, field in self.fields.items():
            field.help_text = f'<small class="text-muted">{field.help_text}</small>'
            # Make density and cost optional
            if field_name in ['density', 'cost']:
                field.required = False 

class UserSettingsForm(forms.ModelForm):
    class Meta:
        model = UserSettings
        fields = ['settings_data'] # Simplified, specific forms for each type might be better

class MachineForm(forms.ModelForm):
    class Meta:
        model = Machine
        fields = [
            'name',
            'maker',
            'model',
            'technology',
            'print_volume_x',
            'print_volume_y',
            'print_volume_z',
            'ip_address',
            'mqtt_access_code',
            'notes'
        ]
        widgets = {
            'notes': forms.Textarea(attrs={'rows': 3}),
            'technology': forms.Select(attrs={'class': 'form-select'}),
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'maker': forms.TextInput(attrs={'class': 'form-control'}),
            'model': forms.TextInput(attrs={'class': 'form-control'}),
            'print_volume_x': forms.NumberInput(attrs={'class': 'form-control'}),
            'print_volume_y': forms.NumberInput(attrs={'class': 'form-control'}),
            'print_volume_z': forms.NumberInput(attrs={'class': 'form-control'}),
            'ip_address': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. 192.168.1.100'}),
            'mqtt_access_code': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Printer access code'}),
        }
        labels = {
            'print_volume_x': 'Print Volume X (mm)',
            'print_volume_y': 'Print Volume Y (mm)',
            'print_volume_z': 'Print Volume Z (mm)',
            'ip_address': 'Printer IP Address',
            'mqtt_access_code': 'MQTT Access Code',
        }

from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User

class FirstSetupForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "email")

    def save(self, commit=True):
        user = super().save(commit=False)
        user.is_staff = True
        user.is_superuser = True
        if commit:
            user.save()
        return user