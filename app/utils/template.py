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

    main_block = "" if not show_main else f"""
            <div class=\"nav-section nav-main\"{main_style}>
                <div class=\"nav-section-title\">Módulo Gmail</div>
                <div class=\"nav-item nav-item-gmail-dashboard{active_class(active_main, 'gmail')}\">
                    <a href=\"/gmail\" class=\"nav-link\">
                        <span class=\"nav-icon\">&#128231;</span>
                        <span class=\"nav-text\">Gmail Dashboard</span>
                        <span class=\"nav-status\"></span>
                    </a>
                    <span class=\"nav-tooltip\">Gmail Dashboard</span>
                </div>
                <div class=\"nav-item nav-item-gmail-auth{active_class(active_main, 'gmail-auth')}\">
                    <a href=\"/gmail/auth\" class=\"nav-link\">
                        <span class=\"nav-icon\">&#128273;</span>
                        <span class=\"nav-text\">Authenticate</span>
                    </a>
                    <span class=\"nav-tooltip\">Authenticate</span>
                </div>
                <div class=\"nav-item nav-item-gmail-poll{active_class(active_main, 'gmail-poll')}\">
                    <a href=\"/gmail/poll\" class=\"nav-link\">
                        <span class=\"nav-icon\">&#128257;</span>
                        <span class=\"nav-text\">Poll Attachments</span>
                    </a>
                    <span class=\"nav-tooltip\">Poll Attachments</span>
                </div>
            </div>
    """

    return f"""
    <nav class=\"tech-navbar collapsed\" id=\"techNavbar\">
        <div class=\"navbar-header\">
            <span class=\"navbar-title\">Extratos API</span>
        </div>

        <div class=\"navbar-nav\">
            <div class=\"nav-item nav-item-home\">
                <a href=\"/\" class=\"nav-link\">
                    <span class=\"nav-icon\">&#127968;</span>
                    <span class=\"nav-text\">Pagina Inicial</span>
                </a>
                <span class=\"nav-tooltip\">Pagina Inicial</span>
            </div>
            {main_block}

            <div class=\"nav-section\"{extratos_style}>
                <div class=\"nav-section-title\">Módulo Extratos Baixados</div>
                <div class=\"nav-item nav-item-extratos{active_class(active_extratos, 'extratos')}\">
                    <a href=\"/extratos\" class=\"nav-link\">
                        <span class=\"nav-icon\">&#128196;</span>
                        <span class=\"nav-text\">Extratos</span>
                    </a>
                    <span class=\"nav-tooltip\">Extratos</span>
                </div>
                <div class=\"nav-item nav-item-simulacao{active_class(active_extratos, 'simulacao')}\">
                    <a href=\"/extratos/simular\" class=\"nav-link\">
                        <span class=\"nav-icon\">&#128269;</span>
                        <span class=\"nav-text\">Simulacao</span>
                    </a>
                    <span class=\"nav-tooltip\">Simulacao</span>
                </div>
                <div class=\"nav-item nav-item-reversao-extratos{active_class(active_extratos, 'reversao-extratos')}\">
                    <a href=\"/extratos/reversao\" class=\"nav-link\">
                        <span class=\"nav-icon\">&#128260;</span>
                        <span class=\"nav-text\">Reversao Extratos</span>
                    </a>
                    <span class=\"nav-tooltip\">Reversao Extratos</span>
                </div>
            </div>
        </div>

        <div class=\"navbar-footer\">
            <div class=\"footer-status\"></div>
            <span class=\"footer-text\">Sistema Online</span>
        </div>

        <button class=\"navbar-toggle\" id=\"navbarToggle\">
            <span class=\"arrow\">></span>
        </button>
    </nav>
    """
