// Preload bridge. The page only receives these methods.
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("grokdeck", {
  platform: process.platform,
  minimize: () => ipcRenderer.invoke("window:minimize"),
  toggleMaximize: () => ipcRenderer.invoke("window:toggle-maximize"),
  close: () => ipcRenderer.invoke("window:close"),
  notify: (title, body) => ipcRenderer.invoke("notify", String(title), String(body)),
  pickDirectory: () => ipcRenderer.invoke("grokdeck:pick-directory"),
  openGrokBot: () => ipcRenderer.invoke("grokdeck:open-grok-bot"),
  resizeStart: () => ipcRenderer.send("window:resize-start"),
  resizeBy: (edge, dx, dy) => ipcRenderer.send("window:resize-by", String(edge), Number(dx), Number(dy)),
  resizeEnd: () => ipcRenderer.send("window:resize-end"),
  moveStart: () => ipcRenderer.send("window:move-start"),
  moveBy: (dx, dy) => ipcRenderer.send("window:move-by", Number(dx), Number(dy)),
  moveEnd: () => ipcRenderer.send("window:move-end"),
  setBackground: (color) => ipcRenderer.invoke("window:background", String(color)),
  grokWeb: {
    newChat: () => ipcRenderer.invoke("grok-web:new-chat"),
    open: (bounds, theme) => ipcRenderer.invoke("grok-web:open", bounds, String(theme)),
    resize: (bounds) => ipcRenderer.invoke("grok-web:resize", bounds),
    close: () => ipcRenderer.invoke("grok-web:close"),
    refresh: () => ipcRenderer.invoke("grok-web:refresh"),
    signOut: () => ipcRenderer.invoke("grok-web:sign-out"),
    onState: (listener) => {
      const handler = (_event, state) => listener(state);
      ipcRenderer.on("grok-web:state", handler);
      return () => ipcRenderer.removeListener("grok-web:state", handler);
    },
  },
});
