const KEY='sbarbase.theme';
export type ThemeMode='system'|'light'|'dark';
export function storedTheme():ThemeMode {
 const value=localStorage.getItem(KEY);
 return value==='light'||value==='dark'?value:'system';
}
/** system removes the override so the stylesheet's own media query decides. */
export function applyTheme(mode:ThemeMode) {
 if(mode==='system')delete document.documentElement.dataset.theme;
 else document.documentElement.dataset.theme=mode;
}
export function storeTheme(mode:ThemeMode) {
 if(mode==='system')localStorage.removeItem(KEY);
 else localStorage.setItem(KEY,mode);
}
export function nextTheme(mode:ThemeMode):ThemeMode {
 return mode==='system'?'light':mode==='light'?'dark':'system';
}