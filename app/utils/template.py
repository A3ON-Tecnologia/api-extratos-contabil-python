"""
Helpers de template para renderizacao simples sem engine.
"""

from __future__ import annotations


def render_tech_navbar(
    *,
    active_main: str | None = None,
    active_extratos: str | None = None,
    show_main: bool = True,
    show_extratos: bool = True,
) -> str:
    """Gera o HTML da navbar lateral."""
    active_main = active_main or ""
    active_extratos = active_extratos or ""
    main_style = "" if show_main else ' style="display:none;"'
    extratos_style = "" if show_extratos else ' style="display:none;"'

    def active_class(value: str, target: str) -> str:
        return " active" if value == target else ""

    return f"""
    <nav class="tech-navbar collapsed" id="techNavbar">
        <div class="navbar-header">
            <span class="navbar-title">Extratos API</span>
        </div>

        <div class="navbar-nav">
            <div class="nav-item nav-item-home">
                <a href="/" class="nav-link">
                    <span class="nav-icon">&#127968;</span>
                    <span class="nav-text">Pagina Inicial</span>
                </a>
                <span class="nav-tooltip">Pagina Inicial</span>
            </div>
            <div class="nav-section nav-main"{main_style}>
                <div class="nav-section-title">Módulo MAKE</div>
                <div class="nav-item nav-item-monitor{active_class(active_main, 'monitor')}">
                    <a href="/monitor" class="nav-link">
                        <span class="nav-icon">&#128250;</span>
                        <span class="nav-text">Monitor</span>
                        <span class="nav-status"></span>
                    </a>
                    <span class="nav-tooltip">Monitor</span>
                </div>
                <div class="nav-item nav-item-test{active_class(active_main, 'test')}">
                    <a href="/test" class="nav-link">
                        <span class="nav-icon">&#129514;</span>
                        <span class="nav-text">Test Monitor</span>
                    </a>
                    <span class="nav-tooltip">Test Monitor</span>
                </div>
                <div class="nav-item nav-item-reversao{active_class(active_main, 'reversao')}">
                    <a href="/reversao" class="nav-link">
                        <span class="nav-icon">&#128260;</span>
                        <span class="nav-text">Reversao</span>
                    </a>
                    <span class="nav-tooltip">Reversao</span>
                </div>
            </div>

            <div class="nav-section"{extratos_style}>
                <div class="nav-section-title">Módulo Extratos Baixados</div>
                <div class="nav-item nav-item-extratos{active_class(active_extratos, 'extratos')}">
                    <a href="/extratos" class="nav-link">
                        <span class="nav-icon">&#128196;</span>
                        <span class="nav-text">Extratos</span>
                    </a>
                    <span class="nav-tooltip">Extratos</span>
                </div>
                <div class="nav-item nav-item-simulacao{active_class(active_extratos, 'simulacao')}">
                    <a href="/extratos/simular" class="nav-link">
                        <span class="nav-icon">&#128269;</span>
                        <span class="nav-text">Simulacao</span>
                    </a>
                    <span class="nav-tooltip">Simulacao</span>
                </div>
                <div class="nav-item nav-item-reversao-extratos{active_class(active_extratos, 'reversao-extratos')}">
                    <a href="/extratos/reversao" class="nav-link">
                        <span class="nav-icon">&#128260;</span>
                        <span class="nav-text">Reversao Extratos</span>
                    </a>
                    <span class="nav-tooltip">Reversao Extratos</span>
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
