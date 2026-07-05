/* ============================================================
   OrderBot Admin Dashboard — Application JavaScript
   Self-contained, no external dependencies. ES6+.
   ============================================================ */

(function () {
  'use strict';

  /* ----------------------------------------------------------
     Initialization
     ---------------------------------------------------------- */
  document.addEventListener('DOMContentLoaded', () => {
    initActiveNav();
    initMobileSidebar();
    initStaggerAnimations();
    initFilterSelects();
    initSearchInput();
    initTableRowLinks();
    initCopyButtons();
    initMenuEditForms();
    initRestaurantAddForm();
    initConfirmButtons();
    initLiveEvents();
  });

  /* ----------------------------------------------------------
     1. Sidebar — Active Link Highlighting
     ---------------------------------------------------------- */
  function initActiveNav() {
    const path = window.location.pathname.replace(/\/+$/, '') || '/';
    const navItems = document.querySelectorAll('.nav-item[data-path]');

    let bestMatch = null;
    let bestLen = 0;

    navItems.forEach((item) => {
      const dp = item.getAttribute('data-path');
      if (path === dp || (dp !== '/' && path.startsWith(dp))) {
        if (dp.length > bestLen) {
          bestLen = dp.length;
          bestMatch = item;
        }
      }
    });

    // Fallback: if path is exactly '/', pick the overview link
    if (!bestMatch) {
      bestMatch = document.querySelector('.nav-item[data-path="/"]');
    }

    if (bestMatch) {
      bestMatch.classList.add('active');
    }
  }

  /* ----------------------------------------------------------
     2. Mobile Sidebar Toggle
     ---------------------------------------------------------- */
  function initMobileSidebar() {
    // Close sidebar when pressing Escape
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        document.body.classList.remove('sidebar-open');
      }
    });

    // Close sidebar when clicking a nav item (mobile)
    const navItems = document.querySelectorAll('.sidebar-nav .nav-item');
    navItems.forEach((item) => {
      item.addEventListener('click', () => {
        if (window.innerWidth < 768) {
          document.body.classList.remove('sidebar-open');
        }
      });
    });
  }

  /* ----------------------------------------------------------
     3. Stagger Animations on Page Load
     ---------------------------------------------------------- */
  function initStaggerAnimations() {
    const cards = document.querySelectorAll(
      '.stat-card, .card, .restaurant-card, .menu-category, .detail-section'
    );
    cards.forEach((card, i) => {
      card.style.opacity = '0';
      card.style.transform = 'translateY(10px)';
      card.style.transition = 'opacity 0.4s ease, transform 0.4s ease';
      card.style.transitionDelay = `${i * 0.05}s`;

      // Force reflow then animate
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          card.style.opacity = '1';
          card.style.transform = 'translateY(0)';
        });
      });
    });
  }

  /* ----------------------------------------------------------
     4. Filter Selects — Auto-navigate on Change
     ---------------------------------------------------------- */
  function initFilterSelects() {
    const filterSelects = document.querySelectorAll('.filter-bar .form-select');

    filterSelects.forEach((select) => {
      select.addEventListener('change', () => {
        const form = select.closest('.filter-bar');
        if (!form) return;

        const selects = form.querySelectorAll('.form-select');
        const inputs = form.querySelectorAll('.form-input');
        const url = new URL(window.location.href);

        // Preserve token param
        const params = new URLSearchParams();
        if (url.searchParams.has('token')) {
          params.set('token', url.searchParams.get('token'));
        }

        selects.forEach((s) => {
          const name = s.getAttribute('name');
          const value = s.value;
          if (name && value) {
            params.set(name, value);
          }
        });

        inputs.forEach((inp) => {
          const name = inp.getAttribute('name');
          const value = inp.value.trim();
          if (name && value) {
            params.set(name, value);
          }
        });

        const base = url.pathname;
        window.location.href = `${base}?${params.toString()}`;
      });
    });
  }

  /* ----------------------------------------------------------
     5. Search Input — debounced auto-submit
     ---------------------------------------------------------- */
  function initSearchInput() {
    const searchInput = document.getElementById('filter-search');
    if (!searchInput) return;

    let timer = null;
    searchInput.addEventListener('input', () => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        const url = new URL(window.location.href);
        const params = new URLSearchParams();
        // Preserve existing filters
        const form = searchInput.closest('.filter-bar');
        if (form) {
          form.querySelectorAll('.form-select').forEach((s) => {
            if (s.name && s.value) params.set(s.name, s.value);
          });
        }
        // Set search (or remove if empty)
        const q = searchInput.value.trim();
        if (q) params.set('search', q);
        params.set('offset', '0');  // reset to first page on new search
        // Preserve token if present
        if (url.searchParams.has('token')) {
          params.set('token', url.searchParams.get('token'));
        }
        window.location.href = `${url.pathname}?${params.toString()}`;
      }, 400);  // 400ms debounce
    });
  }

  /* ----------------------------------------------------------
     6. Table Row Click Navigation
     ---------------------------------------------------------- */
  function initTableRowLinks() {
    const rows = document.querySelectorAll('tr[data-href]');
    rows.forEach((row) => {
      row.addEventListener('click', (e) => {
        // Don't navigate if clicking a button/link inside the row
        if (e.target.closest('a, button, .btn')) return;
        window.location.href = row.getAttribute('data-href');
      });
    });
  }

  /* ----------------------------------------------------------
     6. Copy to Clipboard
     ---------------------------------------------------------- */
  function initCopyButtons() {
    document.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-copy]');
      if (!btn) return;

      const text = btn.getAttribute('data-copy');
      navigator.clipboard
        .writeText(text)
        .then(() => {
          window.showToast('Copied to clipboard!', 'success');
        })
        .catch(() => {
          // Fallback
          const ta = document.createElement('textarea');
          ta.value = text;
          ta.style.position = 'fixed';
          ta.style.opacity = '0';
          document.body.appendChild(ta);
          ta.select();
          document.execCommand('copy');
          document.body.removeChild(ta);
          window.showToast('Copied to clipboard!', 'success');
        });
    });
  }

  /* ----------------------------------------------------------
     7. Menu Edit Forms — Submit via Fetch
     ---------------------------------------------------------- */
  function initMenuEditForms() {
    const forms = document.querySelectorAll('.menu-edit-form');
    forms.forEach((form) => {
      form.addEventListener('submit', async (e) => {
        e.preventDefault();

        const submitBtn = form.querySelector('button[type="submit"]');
        const originalText = submitBtn ? submitBtn.textContent : '';
        if (submitBtn) {
          submitBtn.disabled = true;
          submitBtn.textContent = 'Saving…';
        }

        try {
          const formData = new FormData(form);
          const response = await fetch(form.action, {
            method: form.method || 'POST',
            body: formData,
          });

          if (response.ok) {
            const data = await parseResponse(response);
            window.showToast(
              data.message || 'Menu item updated successfully!',
              'success'
            );
          } else {
            const data = await parseResponse(response);
            window.showToast(
              data.message || data.detail || 'Failed to update menu item.',
              'error'
            );
          }
        } catch (err) {
          window.showToast('Network error. Please try again.', 'error');
        } finally {
          if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.textContent = originalText;
          }
        }
      });
    });
  }

  /* ----------------------------------------------------------
     8. Restaurant Add Form — Submit via Fetch
     ---------------------------------------------------------- */
  function initRestaurantAddForm() {
    const forms = document.querySelectorAll('.restaurant-add-form');
    forms.forEach((form) => {
      form.addEventListener('submit', async (e) => {
        e.preventDefault();

        const submitBtn = form.querySelector('button[type="submit"]');
        const originalText = submitBtn ? submitBtn.textContent : '';
        if (submitBtn) {
          submitBtn.disabled = true;
          submitBtn.textContent = 'Adding…';
        }

        try {
          const formData = new FormData(form);
          const response = await fetch(form.action, {
            method: 'POST',
            body: formData,
          });

          if (response.ok) {
            const data = await parseResponse(response);
            window.showToast(
              data.message || 'Restaurant added successfully!',
              'success'
            );
            // Reload to show the new restaurant
            setTimeout(() => window.location.reload(), 1200);
          } else {
            const data = await parseResponse(response);
            window.showToast(
              data.message || data.detail || 'Failed to add restaurant.',
              'error'
            );
          }
        } catch (err) {
          window.showToast('Network error. Please try again.', 'error');
        } finally {
          if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.textContent = originalText;
          }
        }
      });
    });
  }

  /* ----------------------------------------------------------
     9. Confirm Dialogs for Dangerous Actions
     ---------------------------------------------------------- */
  function initConfirmButtons() {
    document.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-confirm]');
      if (!btn) return;

      const message =
        btn.getAttribute('data-confirm') || 'Are you sure you want to proceed?';
      if (!confirm(message)) {
        e.preventDefault();
        e.stopPropagation();
      }
    });
  }

  /* ----------------------------------------------------------
     Utility: Parse Response (JSON or text)
     ---------------------------------------------------------- */
  async function parseResponse(response) {
    const ct = response.headers.get('content-type') || '';
    if (ct.includes('application/json')) {
      return response.json();
    }
    const text = await response.text();
    return { message: text };
  }

  /* ----------------------------------------------------------
     Toast Notification System
     ---------------------------------------------------------- */
  window.showToast = function (message, type) {
    type = type || 'success';

    const container = document.getElementById('toast-container');
    if (!container) return;

    const icons = {
      success: '✅',
      error: '❌',
      warning: '⚠️',
      info: 'ℹ️',
    };

    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.innerHTML = `
      <span class="toast-icon">${icons[type] || icons.success}</span>
      <span class="toast-message">${escapeHtml(message)}</span>
    `;

    container.appendChild(toast);

    // Auto-dismiss after 4 seconds
    const timer = setTimeout(() => dismissToast(toast), 4000);

    // Click to dismiss early
    toast.addEventListener('click', () => {
      clearTimeout(timer);
      dismissToast(toast);
    });
  };

  function dismissToast(toast) {
    if (!toast || toast._dismissed) return;
    toast._dismissed = true;
    toast.classList.add('toast-dismiss');
    toast.addEventListener('animationend', () => {
      toast.remove();
    });
  }

  /* ----------------------------------------------------------
     Live Events (SSE) — real-time new-order notifications
     ---------------------------------------------------------- */
  function initLiveEvents() {
    try {
      const url = new URL('/events', window.location.href);
      // Pass token via query param for EventSource (cookies don't work)
      const token = new URLSearchParams(window.location.search).get('token');
      if (token) url.searchParams.set('token', token);

      const es = new EventSource(url.toString());
      es.addEventListener('new_order', (e) => {
        try {
          const data = JSON.parse(e.data);
          window.showToast(
            `New order from ${data.customer} at ${data.restaurant} — $${data.total.toFixed(2)}`,
            'info'
          );
        } catch (_) { /* ignore malformed events */ }
      });
      es.onerror = () => {
        // EventSource auto-reconnects; no action needed
      };
    } catch (_) {
      // SSE not supported or connection failed — degrade silently
    }
  }

  function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }
})();
