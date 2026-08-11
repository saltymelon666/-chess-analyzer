let chessLibraryPromise;

export function loadChessLibrary() {
  if (globalThis.Chess) return Promise.resolve();
  if (chessLibraryPromise) return chessLibraryPromise;

  chessLibraryPromise = new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = new URL("./chess.js", import.meta.url).href;
    script.async = true;
    script.onload = () => resolve();
    script.onerror = () => reject(new Error("棋谱解析模块加载失败，请刷新后重试"));
    document.head.appendChild(script);
  });
  return chessLibraryPromise;
}
