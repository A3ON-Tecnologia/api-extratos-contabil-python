/**
 * Tech Navbar Component - JavaScript
 * Controla o toggle da navbar e salva preferência no localStorage
 */

class TechNavbar {
    constructor() {
        this.navbar = document.getElementById('techNavbar');
        this.mainContent = document.getElementById('mainContent');
        this.toggleBtn = document.getElementById('navbarToggle');
        this.storageKey = 'techNavbarCollapsed';

        this.init();
    }

    init() {
        // Carrega preferência salva
        const isCollapsed = localStorage.getItem(this.storageKey) === 'true';
        this.setCollapsed(isCollapsed);

        // Event listener do botão toggle
        if (this.toggleBtn) {
            this.toggleBtn.addEventListener('click', () => this.toggle());
        }

        // Marca item ativo baseado na URL
        this.setActiveItem();
    }

    toggle() {
        const isCollapsed = this.navbar.classList.contains('collapsed');
        this.setCollapsed(!isCollapsed);
    }

    setCollapsed(collapsed) {
        if (collapsed) {
            this.navbar.classList.add('collapsed');
            this.navbar.classList.remove('expanded');
            this.mainContent?.classList.add('navbar-collapsed');
            this.mainContent?.classList.remove('navbar-expanded');
        } else {
            this.navbar.classList.remove('collapsed');
            this.navbar.classList.add('expanded');
            this.mainContent?.classList.remove('navbar-collapsed');
            this.mainContent?.classList.add('navbar-expanded');
        }

        // Salva preferência
        localStorage.setItem(this.storageKey, collapsed.toString());
    }

    setActiveItem() {
        const currentPath = window.location.pathname;
        const navItems = document.querySelectorAll('.nav-item');

        navItems.forEach(item => {
            const link = item.querySelector('.nav-link');
            if (link) {
                const href = link.getAttribute('href');
                if (href === currentPath || (currentPath === '/' && href === '/monitor')) {
                    item.classList.add('active');
                } else {
                    item.classList.remove('active');
                }
            }
        });
    }
}

// HTML Template da Navbar
function getTechNavbarHTML(currentPage = '') {
    return `
    <nav class="tech-navbar collapsed" id="techNavbar">
        <!-- Decorative Elements -->
        <div class="circuit-lines">
            <div class="circuit-dot"></div>
            <div class="circuit-dot"></div>
            <div class="circuit-dot"></div>
        </div>
        
        <!-- Header -->
        <div class="navbar-header">
            <div class="navbar-logo">📊</div>
            <span class="navbar-title">Extratos API</span>
        </div>
        
        <!-- Navigation -->
        <div class="navbar-nav">
            <div class="nav-item ${currentPage === 'monitor' ? 'active' : ''}">
                <a href="/monitor" class="nav-link">
                    <span class="nav-icon">📺</span>
                    <span class="nav-text">Monitor</span>
                    <span class="nav-status"></span>
                </a>
                <span class="nav-tooltip">Monitor</span>
            </div>
            
            <div class="nav-item ${currentPage === 'test' ? 'active' : ''}">
                <a href="/test" class="nav-link">
                    <span class="nav-icon">🧪</span>
                    <span class="nav-text">Modo Teste</span>
                </a>
                <span class="nav-tooltip">Modo Teste</span>
            </div>
            
            <div class="nav-item ${currentPage === 'reversao' ? 'active' : ''}">
                <a href="/reversao" class="nav-link">
                    <span class="nav-icon">🔄</span>
                    <span class="nav-text">Reversão</span>
                </a>
                <span class="nav-tooltip">Reversão</span>
            </div>
        </div>
        
        <!-- Footer -->
        <div class="navbar-footer">
            <div class="footer-status"></div>
            <span class="footer-text">Sistema Online</span>
        </div>
        
        <!-- Toggle Button -->
        <button class="navbar-toggle" id="navbarToggle">
            <span class="arrow">❯</span>
        </button>
    </nav>
    `;
}

// Inicializa quando o DOM estiver pronto
document.addEventListener('DOMContentLoaded', () => {
    new TechNavbar();
});
