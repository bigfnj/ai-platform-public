// madhack -- the early-90s looping "mad hacker" pixel-art gif.
// Source: madhack_scaled_10x_slightly_enhanced.webp -- 960x960 animated WebP (colour + alpha).
// Shown at 48x48 in the shell (same placement as before), so the browser supersamples it down.
//
// An asset URL, not the data: URL it used to be. Inlined, its 7,568 base64 characters sat in
// the main chunk that every visitor downloads before the login form renders, for an image one
// account sees. As a file (5,674 bytes, over Vite's 4 KB inline limit, so it stays a file) the
// browser fetches it only when that <img> renders.
import madhackUrl from './madhack.webp'

export const madhackGif: string = madhackUrl
