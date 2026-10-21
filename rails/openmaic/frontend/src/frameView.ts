// What the OpenMAIC frame area shows, kept free of React and of every import so plain node can
// test it (frontend/tests/frame-view.test.js).
//
// The status poll's app.reachable is a 5 s probe of the app container's /api/health, and the
// rail treats a timeout or a 5xx as "down". It used to unmount the iframe on the FIRST false, so
// one slow probe while the app was busy generating a class (dozens of model calls, minutes of
// work) threw away the page the user was in the middle of, and the next good poll mounted a
// fresh one at the start. Once the app has answered in this mount, the frame stays mounted; a
// later failed probe only adds a warning above it, and the user decides whether to Reload.

export type FrameView = 'frame' | 'down' | 'frame-warning'

/** `reachable` is the latest poll's answer (undefined before the first one lands);
 *  `everReachable` is whether any poll in this mount has said the app answered. */
export function frameView(reachable: boolean | undefined, everReachable: boolean): FrameView {
  if (reachable === false) return everReachable ? 'frame-warning' : 'down'
  // Before the first poll, and while it is up, the frame is mounted: guessing "down" before an
  // answer would flash the error panel on every mount of a healthy rail.
  return 'frame'
}
