"""
Helpers de template para renderizacao simples sem engine.
"""

from __future__ import annotations


def render_tech_navbar(
    *,
    active_main: str | None = None,
    active_extratos: str | None = None,
    show_main: bool = True,
    show_extratos: bool = False,
    show_extratos_test: bool = False,
) -> str:
    """Gera o HTML da navbar lateral."""
    active_main = active_main or ""
    active_extratos = active_extratos or ""
    main_style = "" if show_main else ' style="display:none;"'
    extratos_style = "" if show_extratos else ' style="display:none;"'
    extratos_test_style = "" if show_extratos_test else ' style="display:none;"'

    def active_class(value: str, target: str) -> str:
        return " active" if value == target else ""

    return f"""
    <nav class="tech-navbar collapsed" id="techNavbar">
        <div class="circuit-lines">
            <div class="circuit-dot"></div>
            <div class="circuit-dot"></div>
            <div class="circuit-dot"></div>
        </div>

        <div class="navbar-header">
            <div class="navbar-logo">&#128202;</div>
            <span class="navbar-title">Extratos API</span>
        </div>

        <div class="navbar-nav">
            <div class="nav-main"{main_style}>
                <div class="nav-item{active_class(active_main, 'monitor')}">
                    <a href="/monitor" class="nav-link">
                        <span class="nav-icon">&#128250;</span>
                        <span class="nav-text">Monitor</span>
                        <span class="nav-status"></span>
                    </a>
                    <span class="nav-tooltip">Monitor</span>
                </div>
                <div class="nav-item{active_class(active_main, 'test')}">
                    <a href="/test" class="nav-link">
                        <span class="nav-icon">&#129514;</span>
                        <span class="nav-text">Modo Teste</span>
                    </a>
                    <span class="nav-tooltip">Modo Teste</span>
                </div>
                <div class="nav-item{active_class(active_main, 'reversao')}">
                    <a href="/reversao" class="nav-link">
                        <span class="nav-icon">&#128260;</span>
                        <span class="nav-text">Reversao</span>
                    </a>
                    <span class="nav-tooltip">Reversao</span>
                </div>
            </div>

            <div class="nav-section"{extratos_style}>
                <div class="nav-section-title">Extratos Baixados</div>
                <div class="nav-item{active_class(active_extratos, 'extratos')}">
                    <a href="/extratos" class="nav-link">
                        <span class="nav-icon">&#128196;</span>
                        <span class="nav-text">Extratos</span>
                    </a>
                    <span class="nav-tooltip">Extratos</span>
                </div>
                <div class="nav-item{active_class(active_extratos, 'simulacao')}">
                    <a href="/extratos/simular" class="nav-link">
                        <span class="nav-icon">&#128269;</span>
                        <span class="nav-text">Simulacao</span>
                    </a>
                    <span class="nav-tooltip">Simulacao</span>
                </div>
                <div class="nav-item{active_class(active_extratos, 'monitor-extratos')}">
                    <a href="/extratos/monitor" class="nav-link">
                        <span class="nav-icon">&#128250;</span>
                        <span class="nav-text">Monitor Extratos</span>
                    </a>
                    <span class="nav-tooltip">Monitor Extratos</span>
                </div>
                <div class="nav-item{active_class(active_extratos, 'monitor-teste')}">
                    <a href="/extratos/monitor/test" class="nav-link">
                        <span class="nav-icon">&#129514;</span>
                        <span class="nav-text">Monitor Teste</span>
                    </a>
                    <span class="nav-tooltip">Monitor Teste</span>
                </div>
                <div class="nav-item{active_class(active_extratos, 'reversao-extratos')}">
                    <a href="/extratos/reversao" class="nav-link">
                        <span class="nav-icon">&#128260;</span>
                        <span class="nav-text">Reversao Extratos</span>
                    </a>
                    <span class="nav-tooltip">Reversao Extratos</span>
                </div>
                <div class="nav-item{active_class(active_extratos, 'teste')}"{extratos_test_style}>
                    <a href="/extratos/teste" class="nav-link">
                        <span class="nav-icon">&#129514;</span>
                        <span class="nav-text">Teste Extratos</span>
                    </a>
                    <span class="nav-tooltip">Teste Extratos</span>
                </div>
            </div>
        </div>

        <div class="navbar-footer">
            <div class="footer-status"></div>
            <span class="footer-text">Sistema Online</span>
        </div>

        <button class="navbar-toggle" id="navbarToggle">
            <span class="arrow">></span>
        </button>
    </nav>
    """
