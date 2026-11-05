"use strict";
document.getElementById("btn-imprimir").addEventListener("click", () => window.print());
window.addEventListener("load", () => setTimeout(() => window.print(), 300)); // já abre a janela de impressão
