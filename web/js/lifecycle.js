// 页面卸载回调：路由切换前执行，用来清理定时器 / 事件监听 / 音频任务。

const callbacks = [];

export function onUnmount(fn) {
  if (typeof fn === "function") callbacks.push(fn);
}

export function runUnmount() {
  while (callbacks.length) {
    const fn = callbacks.pop();
    try { fn(); } catch (err) { console.error("unmount cleanup failed", err); }
  }
}
