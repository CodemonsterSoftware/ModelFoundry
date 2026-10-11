// Common JavaScript functionality for ModelFoundry

document.addEventListener('DOMContentLoaded', function() {
    // Initialize tooltips
    var tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
    var tooltipList = tooltipTriggerList.map(function (tooltipTriggerEl) {
        return new bootstrap.Tooltip(tooltipTriggerEl);
    });

    // Initialize popovers
    var popoverTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="popover"]'));
    var popoverList = popoverTriggerList.map(function (popoverTriggerEl) {
        return new bootstrap.Popover(popoverTriggerEl);
    });

    // Handle form submissions with confirmation
    const confirmForms = document.querySelectorAll('form[data-confirm]');
    confirmForms.forEach(form => {
        form.addEventListener('submit', function(e) {
            e.preventDefault();
            MF.confirm({ message: form.dataset.confirm, danger: true }).then(ok => { if (ok) form.submit(); });
        });
    });

    // Handle dynamic form fields
    const addFormFieldButtons = document.querySelectorAll('.add-form-field');
    addFormFieldButtons.forEach(button => {
        button.addEventListener('click', function() {
            const template = document.querySelector(this.dataset.template);
            const container = document.querySelector(this.dataset.container);
            if (template && container) {
                const clone = template.content.cloneNode(true);
                container.appendChild(clone);
            }
        });
    });

    // Handle dynamic form field removal
    document.addEventListener('click', function(e) {
        if (e.target.classList.contains('remove-form-field')) {
            e.preventDefault();
            e.target.closest('.form-field').remove();
        }
    });

    // Handle file input preview
    const fileInputs = document.querySelectorAll('input[type="file"]');
    fileInputs.forEach(input => {
        input.addEventListener('change', function() {
            const preview = document.querySelector(this.dataset.preview);
            if (preview && this.files && this.files[0]) {
                const reader = new FileReader();
                reader.onload = function(e) {
                    preview.src = e.target.result;
                };
                reader.readAsDataURL(this.files[0]);
            }
        });
    });
}); 