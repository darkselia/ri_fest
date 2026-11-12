document.addEventListener("alpine:init", () => {
    const unfoldTheme = window.theme;
    const desktop = window.matchMedia("(min-width: 800px)");
    const savedOpen = () => desktop.matches && localStorage.getItem("sidebarOpen") !== "0";

    window.theme = (...args) => {
        const state = unfoldTheme(...args);
        state.sidebarOpen = savedOpen;
        state.sidebarToggle = function () {
            this.sidebarOpen = !this.sidebarOpen;
            if (desktop.matches) localStorage.setItem("sidebarOpen", this.sidebarOpen ? "1" : "0");
        };
        state.themeBindings["x-resize.window"] = function () {
            this.sidebarOpen = savedOpen();
        };
        return state;
    };
});
