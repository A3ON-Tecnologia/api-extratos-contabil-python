/**
 * Tech Navbar Component - JavaScript
 * Controla o toggle da navbar e salva preferencia no localStorage
 */

class TechNavbar {
    constructor() {
        this.navbar = document.getElementById('techNavbar');
        this.mainContent = document.getElementById('mainContent');
        this.toggleBtn = document.getElementById('navbarToggle');
        this.storageKey = 'techNavbarCollapsed';

        if (!this.navbar) {
            return;
        }

        this.init();
    }

    init() {
        // Carrega preferencia salva
        const isCollapsed = localStorage.getItem(this.storageKey) === 'true';
        this.setCollapsed(isCollapsed);

        // Event listener do botao toggle
        if (this.toggleBtn) {
            this.toggleBtn.addEventListener('click', () => this.toggle());
        }

        // Marca item ativo baseado na URL se nenhum ativo existir
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

        // Salva preferencia
        localStorage.setItem(this.storageKey, collapsed.toString());
    }

    setActiveItem() {
        const navItems = document.querySelectorAll('.nav-item');
        const hasActive = Array.from(navItems).some(item => item.classList.contains('active'));
        if (hasActive) {
            return;
        }

        const currentPath = window.location.pathname;
        navItems.forEach(item => {
            const link = item.querySelector('.nav-link');
            if (link) {
                const href = link.getAttribute('href');
                if (href === currentPath || (currentPath === '/' && href === '/gmail')) {
                    item.classList.add('active');
                } else {
                    item.classList.remove('active');
                }
            }
        });
    }
}

// Inicializa quando o DOM estiver pronto
document.addEventListener('DOMContentLoaded', () => {
    new TechNavbar();
});
