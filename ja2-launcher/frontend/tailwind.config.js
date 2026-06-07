/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // JA2-themed palette: olive drab + amber accents (matches the 1.13 interface)
        ja2: {
          bg: "#1a1c14",       // deep olive black (main background)
          panel: "#26291d",    // panel background
          border: "#3d4332",   // panel borders
          text: "#d4cc9f",     // off-white amber (body text)
          dim: "#8b8a6b",      // muted text
          accent: "#c9a64a",   // amber (active highlights, buttons)
          accentDark: "#8a6f30",
          danger: "#a04632",   // muted red for warnings
        },
      },
      fontFamily: {
        // System font stack — Tauri renders via webview so we get the OS UI font
        sans: ["system-ui", "-apple-system", "Segoe UI", "Roboto", "sans-serif"],
      },
    },
  },
  plugins: [],
};
