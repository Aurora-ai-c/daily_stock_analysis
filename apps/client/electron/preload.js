'use strict';

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('dsa', {
  getBackendStatus: () => ipcRenderer.invoke('dsa:getBackendStatus'),
  restartBackend: () => ipcRenderer.invoke('dsa:restartBackend'),
  getAppVersion: () => ipcRenderer.invoke('dsa:getAppVersion'),
  openDataDir: () => ipcRenderer.invoke('dsa:openDataDir'),
  quitApp: () => ipcRenderer.invoke('dsa:quitApp'),
  setActiveAnalysis: (value) => ipcRenderer.invoke('dsa:setActiveAnalysis', value),
  searxngStatus: () => ipcRenderer.invoke('dsa:searxngStatus'),
  searxngStart: () => ipcRenderer.invoke('dsa:searxngStart'),
  searxngStop: () => ipcRenderer.invoke('dsa:searxngStop'),
  remoteStatus: () => ipcRenderer.invoke('dsa:remoteStatus'),
  setRemoteMode: (enabled) => ipcRenderer.invoke('dsa:setRemoteMode', enabled),
  getLanAddresses: () => ipcRenderer.invoke('dsa:getLanAddresses'),
  cloudflaredStart: () => ipcRenderer.invoke('dsa:cloudflaredStart'),
  cloudflaredStop: () => ipcRenderer.invoke('dsa:cloudflaredStop'),
});
