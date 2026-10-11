// Shared UI helpers for ModelFoundry (window.MF). Loaded before page scripts.
(function () {
    'use strict';

    const TOAST_ICONS = {
        success: 'fa-check-circle text-success',
        error: 'fa-exclamation-circle text-danger',
        warning: 'fa-exclamation-triangle text-warning',
        info: 'fa-info-circle text-info',
    };

    const MF = {};

    MF.esc = function (s) {
        return String(s ?? '').replace(/[&<>"']/g, c => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
        }[c]));
    };

    MF.toast = function (message, level = 'success') {
        if (!TOAST_ICONS[level]) level = 'info';
        const container = document.querySelector('.toast-container');
        if (!container) return;
        const el = document.createElement('div');
        el.className = 'toast';
        el.setAttribute('role', 'alert');
        el.setAttribute('aria-live', 'assertive');
        el.setAttribute('aria-atomic', 'true');
        el.innerHTML =
            '<div class="toast-header">' +
            `<i class="fas ${TOAST_ICONS[level]} me-2"></i>` +
            `<strong class="me-auto">${level.charAt(0).toUpperCase() + level.slice(1)}</strong>` +
            '<button type="button" class="btn-close" data-bs-dismiss="toast" aria-label="Close"></button>' +
            '</div><div class="toast-body"></div>';
        el.querySelector('.toast-body').textContent = message;
        container.appendChild(el);
        el.addEventListener('hidden.bs.toast', () => el.remove());
        new bootstrap.Toast(el, { autohide: true, delay: level === 'error' ? 8000 : 5000 }).show();
    };

    MF.redirectWithToast = function (url, message, level = 'success') {
        sessionStorage.setItem('mf_toast', JSON.stringify({ message, level }));
        location.href = url;
    };

    MF.reloadWithToast = function (message, level = 'success') {
        MF.redirectWithToast(location.href, message, level);
    };

    let settlePendingConfirm = null;

    MF.confirm = function ({ title = 'Are you sure?', message = '', confirmLabel = 'Confirm', danger = false } = {}) {
        // A new prompt supersedes any still-open one (e.g. reopened mid fade-out).
        if (settlePendingConfirm) settlePendingConfirm(false);
        return new Promise(resolve => {
            const modalEl = document.getElementById('mfConfirmModal');
            const okBtn = document.getElementById('mfConfirmOk');
            document.getElementById('mfConfirmTitle').textContent = title;
            document.getElementById('mfConfirmBody').textContent = message;
            okBtn.textContent = confirmLabel;
            okBtn.className = danger ? 'btn btn-danger' : 'btn btn-primary';

            const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
            const finish = value => {
                if (settlePendingConfirm !== finish) return;
                settlePendingConfirm = null;
                okBtn.removeEventListener('click', onOk);
                modalEl.removeEventListener('hide.bs.modal', onHide);
                resolve(value);
            };
            const onOk = () => { finish(true); modal.hide(); };
            const onHide = () => finish(false);
            settlePendingConfirm = finish;
            okBtn.addEventListener('click', onOk, { once: true });
            modalEl.addEventListener('hide.bs.modal', onHide, { once: true });
            modal.show();
            if (!modalEl.dataset.mfStackFix) {
                // Bootstrap clears body.modal-open on hide even when the confirm was stacked
                // over another modal; restore it so the underlying modal keeps scroll lock.
                modalEl.dataset.mfStackFix = '1';
                modalEl.addEventListener('hidden.bs.modal', () => {
                    if (document.querySelector('.modal.show')) document.body.classList.add('modal-open');
                });
            }
        });
    };

    // Phase stepper helpers for the shared upload progress modal
    MF.setPhase = function (phaseName, status) {
        ['Upload', 'Thumbnails', 'Volumes'].forEach(p => {
            $(`#phase${p}`).removeClass('active completed skipped');
        });

        const phaseOrder = ['Upload', 'Thumbnails', 'Volumes'];
        const idx = phaseOrder.indexOf(phaseName);

        for (let i = 0; i < idx; i++) {
            $(`#phase${phaseOrder[i]}`).addClass('completed');
            $(`#phase${phaseOrder[i]}Icon`).html('<i class="fas fa-check"></i>');
        }

        if (phaseName !== 'complete') {
            $(`#phase${phaseName}`).addClass('active');
            $(`#phase${phaseName}Status`).text(status);
        } else {
            phaseOrder.forEach(p => {
                $(`#phase${p}`).removeClass('active').addClass('completed');
                $(`#phase${p}Icon`).html('<i class="fas fa-check"></i>');
            });
        }
    };

    MF.markSkipped = function (phaseName, reason) {
        $(`#phase${phaseName}`).removeClass('active').addClass('skipped');
        $(`#phase${phaseName}Icon`).html('<i class="fas fa-forward"></i>');
        $(`#phase${phaseName}Status`).text(reason || 'Skipped');
    };

    if (window.jQuery) {
        $.ajaxSetup({
            beforeSend(xhr, s) {
                if (!/^(GET|HEAD|OPTIONS)$/i.test(s.type)) {
                    const meta = document.querySelector('meta[name="csrf-token"]');
                    if (meta) xhr.setRequestHeader('X-CSRFToken', meta.content);
                }
            },
        });
    }

    document.addEventListener('DOMContentLoaded', () => {
        const pending = sessionStorage.getItem('mf_toast');
        if (!pending) return;
        sessionStorage.removeItem('mf_toast');
        try {
            const { message, level } = JSON.parse(pending);
            if (message) MF.toast(message, level);
        } catch (e) { /* ignore malformed payload */ }
    });

    window.MF = MF;
})();
