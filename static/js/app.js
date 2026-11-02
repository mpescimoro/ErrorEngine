/**
 * ErrorEngine Monitor - Main JavaScript
 * Handles UI interactions, API calls, form validation, and notifications
 */

// ============================================
// Utility Functions
// ============================================

/**
 * Escape HTML to prevent XSS
 */
function escapeHtml(text) {
    if (text === null || text === undefined) return '';
    const div = document.createElement('div');
    div.textContent = String(text);
    return div.innerHTML;
}

/**
 * Asks for confirmation before submitting forms with data-confirm (e.g. deletions).
 * The text lives in an attribute, not in inline JavaScript: a name with apostrophes
 * or with code in it stays text, and the confirmation always appears.
 */
document.addEventListener('submit', (event) => {
    const message = event.target.dataset ? event.target.dataset.confirm : null;
    if (message && !window.confirm(message)) {
        event.preventDefault();
    }
});

/**
 * Format date for display
 */
function formatDate(dateStr, format = 'short') {
    if (!dateStr) return '-';
    const date = new Date(dateStr);
    if (isNaN(date.getTime())) return dateStr;
    
    if (format === 'short') {
        return date.toLocaleDateString('en-GB', { 
            day: '2-digit', 
            month: '2-digit',
            hour: '2-digit',
            minute: '2-digit'
        });
    }
    
    return date.toLocaleString('en-GB');
}

/**
 * Format relative time (e.g., "2 minutes ago")
 */
function formatRelativeTime(dateStr) {
    if (!dateStr) return '-';
    const date = new Date(dateStr);
    const now = new Date();
    const diffMs = now - date;
    const diffMins = Math.floor(diffMs / 60000);
    const diffHours = Math.floor(diffMins / 60);
    const diffDays = Math.floor(diffHours / 24);
    
    if (diffMins < 1) return 'Just now';
    if (diffMins < 60) return `${diffMins} min ago`;
    if (diffHours < 24) return `${diffHours} h ago`;
    if (diffDays < 7) return `${diffDays} days ago`;
    
    return formatDate(dateStr, 'short');
}

/**
 * Debounce function
 */
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

// ============================================
// API Client
// ============================================

const api = {
    /**
     * Make API request
     */
    async request(url, method = 'GET', data = null) {
        const options = {
            method,
            headers: {
                'Content-Type': 'application/json',
                'Accept': 'application/json'
            }
        };
        
        if (data && method !== 'GET') {
            options.body = JSON.stringify(data);
        }
        
        try {
            const response = await fetch(url, options);
            const result = await response.json();
            
            if (!response.ok) {
                throw new Error(result.message || `HTTP ${response.status}`);
            }
            
            return result;
        } catch (error) {
            if (error.name === 'TypeError' && error.message.includes('fetch')) {
                throw new Error('Connection error. Check your network.');
            }
            throw error;
        }
    },
    
    get(url) { return this.request(url, 'GET'); },
    post(url, data) { return this.request(url, 'POST', data); },
    put(url, data) { return this.request(url, 'PUT', data); },
    delete(url) { return this.request(url, 'DELETE'); }
};

// ============================================
// Toast Notifications
// ============================================

const toast = {
    container: null,
    
    init() {
        if (!this.container) {
            this.container = document.createElement('div');
            this.container.className = 'toast-container';
            this.container.setAttribute('role', 'alert');
            this.container.setAttribute('aria-live', 'polite');
            document.body.appendChild(this.container);
        }
    },
    
    show(message, type = 'info', duration = 4000) {
        this.init();
        
        const icons = {
            success: 'bi-check-circle-fill',
            warning: 'bi-exclamation-triangle-fill',
            danger: 'bi-x-circle-fill',
            info: 'bi-info-circle-fill'
        };
        
        const toastEl = document.createElement('div');
        toastEl.className = `toast toast-${type}`;
        toastEl.innerHTML = `
            <i class="toast-icon bi ${icons[type] || icons.info}"></i>
            <div class="toast-content">${escapeHtml(message)}</div>
            <button class="toast-close" aria-label="Close notification">&times;</button>
        `;
        
        this.container.appendChild(toastEl);
        
        const closeBtn = toastEl.querySelector('.toast-close');
        const hide = () => {
            toastEl.classList.add('hiding');
            setTimeout(() => toastEl.remove(), 300);
        };
        
        closeBtn.addEventListener('click', hide);
        
        if (duration > 0) {
            setTimeout(hide, duration);
        }
        
        return toastEl;
    },
    
    success(message) { return this.show(message, 'success'); },
    warning(message) { return this.show(message, 'warning'); },
    error(message) { return this.show(message, 'danger'); },
    info(message) { return this.show(message, 'info'); }
};

// ============================================
// Form Validation
// ============================================

const validator = {
    patterns: {
        email: /^[^\s@]+@[^\s@]+\.[^\s@]+$/,
        name: /^[\w\s\-àèéìòù]+$/i,
        fieldName: /^[a-zA-Z_][a-zA-Z0-9_]*$/
    },
    
    /**
     * Validate a single field
     */
    validateField(input) {
        const value = input.value.trim();
        const rules = input.dataset;
        let isValid = true;
        let errorMessage = '';
        
        // Required
        if (rules.required !== undefined && !value) {
            isValid = false;
            errorMessage = 'Required field';
        }
        
        // Min length
        if (isValid && rules.minLength && value.length < parseInt(rules.minLength)) {
            isValid = false;
            errorMessage = `Minimum ${rules.minLength} characters`;
        }
        
        // Max length
        if (isValid && rules.maxLength && value.length > parseInt(rules.maxLength)) {
            isValid = false;
            errorMessage = `Maximum ${rules.maxLength} characters`;
        }
        
        // Pattern
        if (isValid && rules.pattern && value) {
            const pattern = this.patterns[rules.pattern];
            if (pattern && !pattern.test(value)) {
                isValid = false;
                errorMessage = rules.patternMessage || 'Invalid format';
            }
        }
        
        // Email
        if (isValid && input.type === 'email' && value) {
            if (!this.patterns.email.test(value)) {
                isValid = false;
                errorMessage = 'Invalid email';
            }
        }
        
        // Email list
        if (isValid && rules.emailList && value) {
            const emails = value.split(',').map(e => e.trim()).filter(e => e);
            for (const email of emails) {
                if (!this.patterns.email.test(email)) {
                    isValid = false;
                    errorMessage = `Invalid email: ${email}`;
                    break;
                }
            }
        }
        
        // Min/max number
        if (isValid && input.type === 'number' && value) {
            const num = parseFloat(value);
            if (input.min && num < parseFloat(input.min)) {
                isValid = false;
                errorMessage = `Minimum value: ${input.min}`;
            }
            if (input.max && num > parseFloat(input.max)) {
                isValid = false;
                errorMessage = `Maximum value: ${input.max}`;
            }
        }
        
        // Update UI
        this.setFieldState(input, isValid, errorMessage);
        
        return isValid;
    },
    
    /**
     * Update field visual state
     */
    setFieldState(input, isValid, errorMessage = '') {
        const formGroup = input.closest('.form-group');
        let errorEl = formGroup?.querySelector('.form-error');
        
        input.classList.toggle('is-invalid', !isValid);
        input.setAttribute('aria-invalid', !isValid);
        
        if (formGroup) {
            if (!isValid && errorMessage) {
                if (!errorEl) {
                    errorEl = document.createElement('div');
                    errorEl.className = 'form-error';
                    errorEl.setAttribute('role', 'alert');
                    formGroup.appendChild(errorEl);
                }
                errorEl.textContent = errorMessage;
                input.setAttribute('aria-describedby', errorEl.id || '');
            } else if (errorEl) {
                errorEl.remove();
            }
        }
    },
    
    /**
     * Validate entire form
     */
    validateForm(form) {
        const inputs = form.querySelectorAll('input, textarea, select');
        let isValid = true;
        let firstInvalid = null;
        
        inputs.forEach(input => {
            if (!this.validateField(input)) {
                isValid = false;
                if (!firstInvalid) firstInvalid = input;
            }
        });
        
        if (firstInvalid) {
            firstInvalid.focus();
        }
        
        return isValid;
    },
    
    /**
     * Setup real-time validation on form
     */
    setupForm(form) {
        const inputs = form.querySelectorAll('input, textarea, select');
        
        inputs.forEach(input => {
            // Validate on blur
            input.addEventListener('blur', () => this.validateField(input));
            
            // Clear error on input
            input.addEventListener('input', debounce(() => {
                if (input.classList.contains('is-invalid')) {
                    this.validateField(input);
                }
            }, 300));
        });
        
        // Validate on submit
        form.addEventListener('submit', (e) => {
            if (!this.validateForm(form)) {
                e.preventDefault();
                toast.warning('Please fix the errors in the form');
            }
        });
    }
};

// ============================================
// Modal Manager
// ============================================

const modal = {
    current: null,
    
    open(modalId) {
        const modalEl = document.getElementById(modalId);
        if (!modalEl) return;
        
        // Create backdrop
        let backdrop = document.querySelector('.modal-backdrop');
        if (!backdrop) {
            backdrop = document.createElement('div');
            backdrop.className = 'modal-backdrop';
            document.body.appendChild(backdrop);
        }
        
        // Show modal
        modalEl.classList.add('show');
        backdrop.classList.add('show');
        document.body.style.overflow = 'hidden';
        
        // Focus first input
        setTimeout(() => {
            const firstInput = modalEl.querySelector('input, textarea, select');
            if (firstInput) firstInput.focus();
        }, 100);
        
        this.current = modalEl;
        
        // Close on backdrop click
        backdrop.onclick = () => this.close();
        
        // Close on escape
        document.addEventListener('keydown', this.handleEscape);
    },
    
    close() {
        if (!this.current) return;
        
        const backdrop = document.querySelector('.modal-backdrop');
        
        this.current.classList.remove('show');
        if (backdrop) backdrop.classList.remove('show');
        document.body.style.overflow = '';
        
        this.current = null;
        document.removeEventListener('keydown', this.handleEscape);
    },
    
    handleEscape(e) {
        if (e.key === 'Escape') {
            modal.close();
        }
    }
};

// ============================================
// Sidebar
// ============================================

const sidebar = {
    init() {
        const toggle = document.querySelector('.sidebar-toggle');
        const sidebarEl = document.querySelector('.sidebar');
        const backdrop = document.querySelector('.sidebar-backdrop');
        
        if (toggle && sidebarEl) {
            toggle.addEventListener('click', () => {
                sidebarEl.classList.toggle('open');
                backdrop?.classList.toggle('show');
            });
            
            backdrop?.addEventListener('click', () => {
                sidebarEl.classList.remove('open');
                backdrop.classList.remove('show');
            });
        }
    }
};

// ============================================
// Clock
// ============================================

const clock = {
    element: null,
    
    init() {
        this.element = document.getElementById('currentTime');
        if (this.element) {
            this.update();
            setInterval(() => this.update(), 1000);
        }
    },
    
    update() {
        if (this.element) {
            this.element.textContent = new Date().toLocaleString('en-GB', {
                weekday: 'short',
                day: '2-digit',
                month: '2-digit',
				year: '2-digit',
                hour: '2-digit',
                minute: '2-digit'
            });
        }
    }
};

// ============================================
// Query Functions
// ============================================

const queryManager = {
    /**
     * Run a query immediately
     */
    async run(queryId) {
        toast.info('Running...');
        
        try {
            const result = await api.post(`/api/queries/${queryId}/run`);
            
            if (result.status === 'success') {
                toast.success(`Done: ${result.rows_returned} rows, ${result.new_errors} new errors`);
            } else if (result.status === 'skipped') {
                toast.warning(result.error_message || 'Skipped: outside schedule');
            } else {
                toast.error(result.error_message || 'Unknown error');
            }
            
            // Reload after delay
            setTimeout(() => location.reload(), 1500);
            
        } catch (error) {
            toast.error(`Error: ${error.message}`);
        }
    },
    
    /**
     * Toggle query active state
     */
    async toggle(queryId) {
        try {
            const result = await api.post(`/api/queries/${queryId}/toggle`);
            toast.success(result.is_active ? 'Query activated' : 'Query deactivated');
            setTimeout(() => location.reload(), 500);
        } catch (error) {
            toast.error(`Error: ${error.message}`);
        }
    }
};

// ============================================
// Error Functions
// ============================================

const errorManager = {
    /**
     * Resolve an error manually
     */
    async resolve(errorId) {
        if (!confirm('Mark this error as resolved?')) return;
        
        try {
            await api.post(`/api/errors/${errorId}/resolve`);
            toast.success('Error marked as resolved');
            setTimeout(() => location.reload(), 500);
        } catch (error) {
            toast.error(`Error: ${error.message}`);
        }
    }
};

// ============================================
// Routing Rules Manager
// ============================================

const routingManager = {
    rules: [],
    operators: [],
    queryId: null,
    
    init(queryId, operators) {
        this.queryId = queryId;
        this.operators = operators || [];
        this.loadRules();
    },
    
    async loadRules() {
        if (!this.queryId) return;
        
        try {
            const data = await api.get(`/api/queries/${this.queryId}/routing/rules`);
            this.rules = data.rules || [];
            this.render();
            
            // Load available fields
            const fieldsData = await api.get(`/api/queries/${this.queryId}/fields`);
            window.availableFields = (fieldsData.fields || []).map(f => f.name);
        } catch (error) {
            console.error('Error loading routing rules:', error);
        }
    },
    
    render() {
        const container = document.getElementById('routingRulesContainer');
        const emptyState = document.getElementById('noRulesMessage');
        
        if (!container) return;
        
        if (this.rules.length === 0) {
            container.innerHTML = '';
            if (emptyState) emptyState.classList.remove('hidden');
            return;
        }
        
        if (emptyState) emptyState.classList.add('hidden');
        
        container.innerHTML = this.rules.map((rule, idx) => `
            <div class="routing-rule">
                <div class="routing-rule-info">
                    <div class="routing-rule-name">
                        ${escapeHtml(rule.name) || `Rule ${idx + 1}`}
                        <span class="badge badge-secondary">${rule.conditions.length} ${rule.conditions.length === 1 ? 'condition' : 'conditions'}</span>
                        ${rule.stop_on_match ? '<span class="badge badge-warning">STOP</span>' : ''}
                        ${!rule.is_active ? '<span class="badge badge-secondary">Paused</span>' : ''}
                    </div>
                    <div class="routing-rule-recipients">→ ${escapeHtml(rule.recipients)}</div>
                </div>
                <div class="actions">
                    <button type="button" class="btn btn-ghost btn-sm btn-icon" 
                            onclick="routingManager.edit(${rule.id})" 
                            aria-label="Edit rule">
                        <i class="bi bi-pencil"></i>
                    </button>
                    <button type="button" class="btn btn-ghost btn-sm btn-icon text-danger" 
                            onclick="routingManager.delete(${rule.id})"
                            aria-label="Delete rule">
                        <i class="bi bi-trash"></i>
                    </button>
                </div>
            </div>
        `).join('');
    },
    
    openModal(rule = null) {
        const isEdit = !!rule;
        
        document.getElementById('ruleModalTitle').textContent = isEdit ? 'Edit rule' : 'New rule';
        document.getElementById('ruleId').value = rule?.id || '';
        document.getElementById('ruleName').value = rule?.name || '';
        document.getElementById('ruleRecipients').value = rule?.recipients || '';
        document.getElementById('ruleConditionLogic').value = rule?.condition_logic || 'AND';
        document.getElementById('rulePriority').value = rule?.priority || 0;
        document.getElementById('ruleStopOnMatch').checked = rule?.stop_on_match || false;
        // A new rule starts active; an existing one keeps its state
        document.getElementById('ruleIsActive').checked = rule ? rule.is_active !== false : true;
        
        const conditionsContainer = document.getElementById('conditionsContainer');
        conditionsContainer.innerHTML = '';
        
        if (rule?.conditions?.length > 0) {
            rule.conditions.forEach(cond => this.addConditionRow(cond));
        }
        
        this.updateConditionsHint();
        modal.open('ruleModal');
    },
    
    addConditionRow(condition = null) {
        const container = document.getElementById('conditionsContainer');
        const fields = window.availableFields || [];
        
        const row = document.createElement('div');
        row.className = 'condition-row';
        row.innerHTML = `
            <div>
                <input type="text" class="form-control cond-field" 
                       placeholder="Field" list="fieldsList"
                       value="${escapeHtml(condition?.field_name || '')}"
                       aria-label="Field name">
                <datalist id="fieldsList">
                    ${fields.map(f => `<option value="${escapeHtml(f)}">`).join('')}
                </datalist>
            </div>
            <div>
                <select class="form-control form-select cond-operator" aria-label="Operator">
                    ${this.operators.map(op => `
                        <option value="${op.value}" ${condition?.operator === op.value ? 'selected' : ''}>
                            ${escapeHtml(op.label)}
                        </option>
                    `).join('')}
                </select>
            </div>
            <div>
                <input type="text" class="form-control cond-value" 
                       placeholder="Value"
                       value="${escapeHtml(condition?.value || '')}"
                       aria-label="Value">
            </div>
            <div class="flex items-center gap-sm">
                <label class="form-check" title="Case sensitive">
                    <input type="checkbox" class="form-check-input cond-case" 
                           ${condition?.case_sensitive ? 'checked' : ''}>
                    <span class="form-check-label">Aa</span>
                </label>
                <button type="button" class="btn btn-ghost btn-sm btn-icon text-danger" 
                        onclick="this.closest('.condition-row').remove(); routingManager.updateConditionsHint()"
                        aria-label="Remove condition">
                    <i class="bi bi-x-lg"></i>
                </button>
            </div>
        `;
        
        container.appendChild(row);
        this.updateConditionsHint();
    },
    
    updateConditionsHint() {
        const container = document.getElementById('conditionsContainer');
        const hint = document.getElementById('noConditionsHint');
        if (hint) {
            hint.classList.toggle('hidden', container.children.length > 0);
        }
    },
    
    async save() {
        const ruleId = document.getElementById('ruleId').value;
        const recipients = document.getElementById('ruleRecipients').value.trim();
        
        // Validate
        if (!recipients) {
            toast.warning('Enter at least one recipient');
            document.getElementById('ruleRecipients').focus();
            return;
        }
        
        // Collect conditions
        const conditions = [];
        document.querySelectorAll('.condition-row').forEach(row => {
            const field = row.querySelector('.cond-field').value.trim();
            if (field) {
                conditions.push({
                    field_name: field,
                    operator: row.querySelector('.cond-operator').value,
                    value: row.querySelector('.cond-value').value,
                    case_sensitive: row.querySelector('.cond-case').checked
                });
            }
        });
        
        const ruleData = {
            name: document.getElementById('ruleName').value.trim(),
            recipients,
            condition_logic: document.getElementById('ruleConditionLogic').value,
            priority: parseInt(document.getElementById('rulePriority').value) || 0,
            stop_on_match: document.getElementById('ruleStopOnMatch').checked,
            is_active: document.getElementById('ruleIsActive').checked,
            conditions
        };
        
        try {
            let result;
            if (ruleId) {
                result = await api.put(`/api/queries/${this.queryId}/routing/rules/${ruleId}`, ruleData);
            } else {
                result = await api.post(`/api/queries/${this.queryId}/routing/rules`, ruleData);
            }
            
            if (result.success) {
                modal.close();
                toast.success('Rule saved');
                this.loadRules();
            } else {
                toast.error(result.message || 'Save failed');
            }
        } catch (error) {
            toast.error(`Error: ${error.message}`);
        }
    },
    
    edit(ruleId) {
        const rule = this.rules.find(r => r.id === ruleId);
        if (rule) {
            this.openModal(rule);
        }
    },
    
    async delete(ruleId) {
        if (!confirm('Delete this rule?')) return;
        
        try {
            const result = await api.delete(`/api/queries/${this.queryId}/routing/rules/${ruleId}`);
            if (result.success) {
                toast.success('Rule deleted');
                this.loadRules();
            }
        } catch (error) {
            toast.error(`Error: ${error.message}`);
        }
    }
};

// ============================================
// Day Picker
// ============================================

const dayPicker = {
    init() {
        const buttons = document.querySelectorAll('.day-picker-btn');
        const hiddenInput = document.getElementById('schedule_days');
        
        if (!buttons.length || !hiddenInput) return;
        
        buttons.forEach(btn => {
            btn.addEventListener('click', () => {
                btn.classList.toggle('active');
                this.updateHiddenInput(buttons, hiddenInput);
            });
        });
    },
    
    updateHiddenInput(buttons, input) {
        const days = [];
        buttons.forEach(btn => {
            if (btn.classList.contains('active')) {
                days.push(btn.dataset.day);
            }
        });
        input.value = days.join(',');
    }
};

// ============================================
// Source Type Toggle
// ============================================

const sourceToggle = {
    init() {
        const select = document.getElementById('source_type');
        if (!select) return;
        
        select.addEventListener('change', () => this.update());
        this.update();
    },
    
    update() {
        const sourceType = document.getElementById('source_type')?.value;
		const databaseSection = document.getElementById('source-database');
        const httpSection = document.getElementById('source-http');
        
		if (databaseSection) {
			databaseSection.classList.toggle('hidden', sourceType !== 'database');
		}
		const dbConnectionGroup = document.getElementById('dbConnectionGroup');
		if (dbConnectionGroup) {
			dbConnectionGroup.style.display = sourceType === 'database' ? 'block' : 'none';
		}
        if (httpSection) {
			httpSection.classList.toggle('hidden', sourceType === 'database');
        }
    }
};

// Global functions for the onclick handlers in the templates
function toggleSourceType() {
    sourceToggle.update();
}

async function testSqlQuery() {
    const connId = document.getElementById('db_connection_id').value;
    const sql = document.getElementById('sql_query').value;
    const container = document.getElementById('testResults');
    
    if (!connId) {
        toast.warning('Select a database connection');
        return;
    }
    if (!sql.trim()) {
        toast.warning('Enter a SQL query');
        return;
    }

    // A statement that changes data gets the same warning as when saving:
    // the test runs it for real on the database
    let confirmWrite = false;
    if (typeof sqlSafety !== 'undefined') {
        const keyword = sqlSafety.check(sql);
        if (keyword) {
            if (!(await sqlSafety.confirm(keyword))) return;
            confirmWrite = true;
        }
    }

    container.classList.remove('hidden');
    container.innerHTML = '<div class="flex items-center justify-center p-lg"><div class="spinner"></div></div>';

    try {
        const result = await api.post(`/api/connections/${connId}/test-query`, { sql, confirm_write: confirmWrite });
        
        if (result.valid) {
            let html = `
                <div class="alert alert-success">
                    <i class="alert-icon bi bi-check-circle-fill"></i>
                    <div class="alert-content">
                        <div class="alert-title">Valid query</div>
                        ${result.row_count} rows returned
                    </div>
                </div>
            `;
            
            if (result.sample_rows && result.sample_rows.length > 0) {
                html += '<div class="table-container mt-md"><table class="table"><thead><tr>';
                result.columns.forEach(col => {
                    html += `<th>${escapeHtml(col)}</th>`;
                });
                html += '</tr></thead><tbody>';
                
                result.sample_rows.slice(0, 5).forEach(row => {
                    html += '<tr>';
                    result.columns.forEach(col => {
                        const val = row[col];
                        html += `<td>${val !== null ? escapeHtml(String(val)) : '<span class="text-muted">NULL</span>'}</td>`;
                    });
                    html += '</tr>';
                });
                html += '</tbody></table></div>';
            }
            
            container.innerHTML = html;
        } else {
            container.innerHTML = `
                <div class="alert alert-danger">
                    <i class="alert-icon bi bi-x-circle-fill"></i>
                    <div class="alert-content">
                        <div class="alert-title">Error</div>
                        ${escapeHtml(result.error)}
                    </div>
                </div>
            `;
        }
    } catch (error) {
        container.innerHTML = `
            <div class="alert alert-danger">
                <i class="alert-icon bi bi-x-circle-fill"></i>
                <div class="alert-content">${escapeHtml(error.message)}</div>
            </div>
        `;
    }
}


// ============================================
// Test Connections
// ============================================

const testConnections = {
    async email(recipient) {
        if (!recipient) {
            toast.warning('Enter an email address');
            return;
        }
        
        const btn = document.getElementById('testEmailBtn');
        if (btn) btn.disabled = true;
        
        try {
            const result = await api.post('/api/test/email', { recipient });
            
            if (result.success) {
                toast.success('Test email sent!');
            } else {
                toast.error(result.message);
            }
        } catch (error) {
            toast.error(`Error: ${error.message}`);
        }
        
        if (btn) btn.disabled = false;
    }
};


// ============================================
// Next Check Timer - Real time countdown
// ============================================

const nextCheckTimer = {
    element: null,
    queryNameElement: null,
    countdownId: null,
    refreshId: null,
    secondsRemaining: 0,
    
    init() {
        this.element = document.getElementById('nextCheckTime');
        this.queryNameElement = document.getElementById('nextCheckQuery');
        if (this.element) {
            this.update();
            // Refresh data from the server every 30 seconds
            this.refreshId = setInterval(() => this.update(), 30000);
            // Visual countdown every second
            this.countdownId = setInterval(() => this.tick(), 1000);
        }
    },
    
    async update() {
        if (!this.element) return;
        
        try {
            const data = await api.get('/api/scheduler/next');
            
            if (data.has_scheduled) {
                this.secondsRemaining = data.seconds_remaining;
                this.element.textContent = this.formatTime(this.secondsRemaining);
                
                if (this.queryNameElement) {
                    this.queryNameElement.textContent = data.query_name;
                    this.queryNameElement.classList.remove('hidden');
                }
            } else {
                this.secondsRemaining = -1;
                this.element.textContent = '—';
                if (this.queryNameElement) {
                    this.queryNameElement.textContent = data.message || 'No active queries';
                    this.queryNameElement.classList.remove('hidden');
                }
            }
        } catch (error) {
            console.error('Error fetching next check:', error);
            this.element.textContent = '—';
        }
    },
    
    tick() {
        if (!this.element || this.secondsRemaining < 0) return;
        
        this.secondsRemaining--;
        
        if (this.secondsRemaining <= 0) {
            this.element.textContent = 'Running...';
            // Reload data from the server after 3 seconds
            setTimeout(() => this.update(), 3000);
        } else {
            this.element.textContent = this.formatTime(this.secondsRemaining);
        }
    },
    
    formatTime(totalSeconds) {
        if (totalSeconds <= 0) return 'Now';
        
        const minutes = Math.floor(totalSeconds / 60);
        const seconds = totalSeconds % 60;
        
        if (minutes >= 60) {
            const hours = Math.floor(minutes / 60);
            const mins = minutes % 60;
            return `${hours}h ${mins}m`;
        }
        
        if (minutes > 0) {
            return `${minutes}m ${seconds.toString().padStart(2, '0')}s`;
        }
        
        return `${seconds}s`;
    }
};


// ============================================
// Dashboard Statistics
// ============================================

const dashboardStats = {
    async init() {
        if (!document.getElementById('statErrorsToday')) return;
        
        await this.loadOverview();
        await this.loadTimeline();
    },
    
    async loadOverview() {
        try {
            const data = await api.get('/api/stats/overview');
            
            document.getElementById('statErrorsToday').textContent = data.errors_today;
            document.getElementById('statErrorsWeek').textContent = data.errors_week;
            document.getElementById('statErrorsActive').textContent = data.errors_active;
            document.getElementById('statAvgResolution').textContent = 
                data.avg_resolution_hours > 0 ? `${data.avg_resolution_hours}h` : '-';
            
            // Trend
            const trendEl = document.getElementById('statTrend');
            if (data.trend_percent !== 0) {
                const icon = data.trend_direction === 'up' ? '↑' : '↓';
                const color = data.trend_direction === 'up' ? 'text-danger' : 'text-success';
                trendEl.innerHTML = `<span class="${color}">${icon} ${Math.abs(data.trend_percent)}%</span>`;
            } else {
                trendEl.innerHTML = '<span class="text-muted">= unchanged</span>';
            }
            
            // Top queries
            const topEl = document.getElementById('topQueries');
            if (data.top_queries.length > 0) {
                let html = '<div class="table-container"><table class="table"><tbody>';
                data.top_queries.forEach((q, i) => {
                    const barWidth = (q.count / data.top_queries[0].count) * 100;
                    html += `
                        <tr>
                            <td style="width: 40px;" class="text-muted">#${i + 1}</td>
                            <td>
                                <a href="/queries/${q.id}">${escapeHtml(q.name)}</a>
                                <div class="mt-xs" style="background: var(--gray-200); height: 4px; border-radius: 2px;">
                                    <div style="background: var(--primary); height: 100%; width: ${barWidth}%; border-radius: 2px;"></div>
                                </div>
                            </td>
                            <td style="width: 60px;" class="text-right">
                                <span class="badge badge-danger">${q.count}</span>
                            </td>
                        </tr>
                    `;
                });
                html += '</tbody></table></div>';
                topEl.innerHTML = html;
            } else {
                topEl.innerHTML = '<p class="text-muted text-center">No errors in the last 30 days</p>';
            }
            
        } catch (error) {
            console.error('Error loading stats:', error);
        }
    },
    
    async loadTimeline() {
        const container = document.getElementById('errorChart');
        if (!container) return;
        
        try {
            const data = await api.get('/api/stats/timeline?days=14');
            
            if (data.length === 0 || data.every(d => d.count === 0)) {
                container.innerHTML = '<p class="text-muted text-center">No data available</p>';
                return;
            }
            
            // Simple chart with CSS bars (no external libraries)
            const maxCount = Math.max(...data.map(d => d.count), 1);
            
            let html = '<div class="flex items-end gap-xs" style="height: 180px;">';
            data.forEach(d => {
                const height = (d.count / maxCount) * 160;
                const heightPx = Math.max(height, d.count > 0 ? 4 : 0);
                html += `
                    <div class="flex-1 flex flex-col items-center">
                        <div class="text-xs text-muted mb-xs">${d.count > 0 ? d.count : ''}</div>
                        <div style="width: 100%; height: ${heightPx}px; background: var(--primary); border-radius: 2px 2px 0 0;" 
                             title="${d.label}: ${d.count} errors"></div>
                        <div class="text-xs text-muted mt-xs">${d.label}</div>
                    </div>
                `;
            });
            html += '</div>';
            
            container.innerHTML = html;
            
        } catch (error) {
            console.error('Error loading timeline:', error);
            container.innerHTML = '<p class="text-danger text-center">Failed to load data</p>';
        }
    }
};


// ============================================
// Error Expandable Details
// ============================================

const errorExpander = {
    toggle(button, errorId) {
        const content = document.getElementById(`error-details-${errorId}`);
        if (!content) return;
        
        const isExpanded = content.classList.contains('show');
        
        // Toggle content
        content.classList.toggle('show');
        
        // Toggle button state
        button.classList.toggle('expanded');
        
        // Update aria
        button.setAttribute('aria-expanded', !isExpanded);
    }
};

// ============================================
// Settings Page - Auto-load stats
// ============================================

const settingsPage = {
    init() {
        // Auto-load cleanup stats if on settings page
        const statsContainer = document.getElementById('cleanupStats');
        if (statsContainer) {
            this.loadCleanupStats();
        }
		this.loadConnectionsStatus();
    },
    
    async loadCleanupStats() {
        const container = document.getElementById('cleanupStats');
        if (!container) return;
        
        container.innerHTML = '<div class="flex items-center justify-center p-md"><div class="spinner"></div></div>';
        
        try {
            const stats = await api.get('/api/cleanup/stats');
            
            container.innerHTML = `
                <div class="grid grid-cols-2 gap-md">
                    <div>
                        <div class="text-muted mb-xs">Run logs</div>
                        <div class="stat-value" style="font-size: 1.5rem;">${stats.counts.query_logs}</div>
                        <div class="text-muted" style="font-size: 0.75rem;">
                            Retention: ${stats.retention_config.log_retention_days} days
                        </div>
                    </div>
                    <div>
                        <div class="text-muted mb-xs">Email log</div>
                        <div class="stat-value" style="font-size: 1.5rem;">${stats.counts.email_logs}</div>
                        <div class="text-muted" style="font-size: 0.75rem;">
                            Retention: ${stats.retention_config.email_log_retention_days} days
                        </div>
                    </div>
                    <div>
                        <div class="text-muted mb-xs">Active errors</div>
                        <div class="stat-value" style="font-size: 1.5rem;">${stats.counts.active_errors}</div>
                    </div>
                    <div>
                        <div class="text-muted mb-xs">Resolved errors</div>
                        <div class="stat-value" style="font-size: 1.5rem;">${stats.counts.resolved_errors}</div>
                        <div class="text-muted" style="font-size: 0.75rem;">
                            Retention: ${stats.retention_config.resolved_errors_retention_days} days
                        </div>
                    </div>
                </div>
            `;
        } catch (error) {
            container.innerHTML = `
                <div class="alert alert-danger">
                    <i class="alert-icon bi bi-x-circle-fill"></i>
                    <div class="alert-content">Error: ${escapeHtml(error.message)}</div>
                </div>
            `;
        }
    },
    
	async loadConnectionsStatus() {
		const container = document.getElementById('connectionsStatus');
		if (!container) return;
		
		try {
			const connections = await api.get('/api/connections');
			
			if (connections.length === 0) {
				container.innerHTML = `
					<div class="alert alert-warning">
						<i class="alert-icon bi bi-exclamation-triangle"></i>
						<div class="alert-content">No connections configured</div>
					</div>
				`;
				return;
			}
			
			let html = '<div class="table-container"><table class="table"><tbody>';
			connections.forEach(conn => {
				html += `
					<tr>
						<td><strong>${escapeHtml(conn.name)}</strong></td>
						<td><span class="badge badge-primary">${escapeHtml(conn.db_label || conn.db_type)}</span></td>
						<td class="text-muted">${escapeHtml(conn.db_type === 'sqlite' ? (conn.database || '') : (conn.host || ''))}</td>
					</tr>
				`;
			});
			html += '</tbody></table></div>';
			html += `<p class="text-muted mt-sm">${connections.length} active ${connections.length === 1 ? 'connection' : 'connections'}</p>`;
			
			container.innerHTML = html;
		} catch (error) {
			container.innerHTML = `
				<div class="alert alert-danger">
					<i class="alert-icon bi bi-x-circle"></i>
					<div class="alert-content">Loading failed: ${escapeHtml(error.message)}</div>
				</div>
			`;
		}
	},
	
    async runCleanup() {
        if (!confirm('Run a full manual cleanup?\n\nThis deletes:\n• All run logs\n• All email logs\n• All resolved errors\n\nActive errors are NOT touched.\n\nContinue?')) return;
        
        try {
            const result = await api.post('/api/cleanup/run');
            
            if (result.success) {
                const r = result.result;
                toast.success(`Cleanup done: ${r.query_logs_deleted} logs, ${r.email_logs_deleted} emails, ${r.resolved_errors_deleted} errors deleted`);
                this.loadCleanupStats();
            } else {
                toast.error(result.message);
            }
        } catch (error) {
            toast.error(`Error: ${error.message}`);
        }
    }
};

// ============================================
// Live Updates
// Every 30 seconds, check whether the active errors have changed:
// always update the counter in the menu and, on pages with
// data-live-reload (Errors, Dashboard), reload the page.
// If the user is working on the page, show a notice instead.
// ============================================

const liveUpdates = {
    intervalMs: 30000,
    signature: null,
    pending: false,

    init() {
        const signature = document.body.dataset.errorsSignature;
        if (!signature) return;
        this.signature = signature;
        setInterval(() => this.check(), this.intervalMs);
        document.addEventListener('visibilitychange', () => {
            if (!document.hidden && this.pending) this.reload();
        });
    },

    async check() {
        let data;
        try {
            const response = await fetch('/api/errors/summary', {
                headers: { 'Accept': 'application/json' },
                cache: 'no-store'
            });
            if (response.status === 401) {
                // Session expired: reloading goes back to the sign-in page
                location.reload();
                return;
            }
            if (!response.ok) return;
            data = await response.json();
        } catch (error) {
            return;  // Server temporarily unreachable: try again at the next round
        }

        this.updateBadge(data.active);

        if (data.signature !== this.signature) {
            this.signature = data.signature;
            if (document.body.dataset.liveReload === 'true') this.pending = true;
        }
        if (this.pending) this.reload();
    },

    updateBadge(count) {
        const badge = document.getElementById('navErrorCount');
        if (!badge) return;
        badge.textContent = count;
        badge.style.display = count > 0 ? '' : 'none';
    },

    reload() {
        if (this.userIsBusy()) {
            this.showBanner();
            return;
        }
        location.reload();
    },

    userIsBusy() {
        if (document.hidden) return false;
        // Selected text (e.g. a code to copy)
        const selection = window.getSelection ? window.getSelection().toString() : '';
        if (selection) return true;
        // An error detail is open
        if (document.querySelector('.expandable-trigger[aria-expanded="true"]')) return true;
        // A field or a menu in use
        const active = document.activeElement;
        return !!active && ['INPUT', 'SELECT', 'TEXTAREA'].includes(active.tagName);
    },

    showBanner() {
        if (document.getElementById('liveUpdateBanner')) return;
        const main = document.querySelector('.main-content');
        if (!main) return;
        const banner = document.createElement('div');
        banner.id = 'liveUpdateBanner';
        banner.className = 'alert alert-info';
        banner.setAttribute('role', 'status');
        banner.innerHTML = `
            <i class="alert-icon bi bi-arrow-repeat"></i>
            <div class="alert-content">Active errors have changed.</div>
            <button type="button" class="btn btn-sm btn-primary">Refresh</button>`;
        banner.querySelector('button').addEventListener('click', () => location.reload());
        main.insertBefore(banner, main.firstChild);
    }
};

// ============================================
// Initialize on DOM Ready
// ============================================

document.addEventListener('DOMContentLoaded', () => {
    sidebar.init();
    liveUpdates.init();
    clock.init();
    nextCheckTimer.init();
    dayPicker.init();
    sourceToggle.init();
    settingsPage.init();
	dashboardStats.init();
    
    // Setup form validation on forms with data-validate
    document.querySelectorAll('form[data-validate]').forEach(form => {
        validator.setupForm(form);
    });
    
    // Initialize alert dismissible
    document.querySelectorAll('.alert-dismissible .alert-close').forEach(btn => {
        btn.addEventListener('click', () => {
            btn.closest('.alert').remove();
        });
    });
});

// ============================================
// Global Exports
// ============================================

window.api = api;
window.toast = toast;
window.modal = modal;
window.validator = validator;
window.queryManager = queryManager;
window.errorManager = errorManager;
window.routingManager = routingManager;
window.testConnections = testConnections;
window.errorExpander = errorExpander;
window.settingsPage = settingsPage;
window.escapeHtml = escapeHtml;
window.formatDate = formatDate;
window.formatRelativeTime = formatRelativeTime;
