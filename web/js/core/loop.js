// 循环播放控制器（与 DOM 解耦，便于测试）
//
// 关键点：
// * 用"音频 ended 事件"驱动下一段，绝不用固定时长估算；
// * 任何时刻只允许一条播放链（runId 递增，旧链自动作废）；
// * 暂停保留（单词、重复进度、口音位置、列表位置）；
// * 停止会清掉定时器并停止音频。

export const REPEAT_OPTIONS = [1, 2, 3, 5, 10, Infinity];
export const INTERVAL_OPTIONS = [0, 1, 2, 3, 5];
export const ACCENT_OPTIONS = [
  { value: "uk", label: "英式发音" },
  { value: "us", label: "美式发音" },
  { value: "both", label: "英音 + 美音" },
];
export const ORDER_OPTIONS = [
  { value: "seq", label: "顺序播放" },
  { value: "random", label: "随机播放" },
];

export const LOOP_DEFAULTS = {
  repeat: 3,          // 每个单词播放次数（Infinity = 无限）
  interval: 1,        // 每段音频结束后的间隔（秒）
  accents: "uk",      // uk | us | both
  order: "seq",       // seq | random
  loopList: false,    // 列表播完后是否从头再来（配合 autoNext）
  autoNext: false,    // 一个词播完是否自动跳到下一个词
};

const shuffle = (items) => {
  const out = items.slice();
  for (let i = out.length - 1; i > 0; i -= 1) {
    const j = Math.floor(Math.random() * (i + 1));
    [out[i], out[j]] = [out[j], out[i]];
  }
  return out;
};

export class LoopController {
  constructor({
    playClip,          // (word, accent) => Promise（ended 时 resolve）
    pauseClip,         // () => void
    resumeClip,        // () => Promise<boolean>
    stopClip,          // () => void
    onUpdate = () => {},
    setTimeoutFn = (fn, ms) => setTimeout(fn, ms),
    clearTimeoutFn = (id) => clearTimeout(id),
  }) {
    this.playClip = playClip;
    this.pauseClip = pauseClip;
    this.resumeClip = resumeClip;
    this.stopClip = stopClip;
    this.onUpdate = onUpdate;
    this.setTimeoutFn = setTimeoutFn;
    this.clearTimeoutFn = clearTimeoutFn;

    this.settings = { ...LOOP_DEFAULTS };
    this.words = [];
    this.status = "idle";      // idle | playing | paused | waiting
    this.index = 0;
    this.rep = 0;
    this.accentPos = 0;
    this.current = null;
    this.singleWord = false;
    this.played = 0;
    this.skipped = 0;
    this.lastError = "";
    this.phase = "idle";       // playing | waiting
    this.clipActive = false;
    this.timer = null;
    this.runId = 0;
    this.order = [];
  }

  setSettings(patch) {
    this.settings = { ...this.settings, ...patch };
    this._emit();
  }

  isActive() {
    return this.status !== "idle";
  }

  snapshot() {
    return {
      status: this.status,
      index: this.index,
      total: this.words.length,
      word: this.current ? this.current.word : null,
      accent: this.current ? this.current.accent : null,
      repeatIndex: this.current ? this.current.displayRep : this.rep,
      repeatTotal: this.settings.repeat,
      played: this.played,
      skipped: this.skipped,
      lastError: this.lastError,
      settings: { ...this.settings },
    };
  }

  /** 从列表第 startIndex 个词开始播放（不传则顺序从 0 / 随机抽一个） */
  start(words, startIndex = null, single = false) {
    this.singleWord = single;
    this.runId += 1;
    this._clearTimer();
    try { this.stopClip(); } catch (_) { /* ignore */ }
    this.clipActive = false;
    this.words = Array.isArray(words) ? words.slice() : [];
    this.index = 0;
    this.rep = 0;
    this.accentPos = 0;
    this.current = null;
    this.played = 0;
    this.skipped = 0;
    this.lastError = "";
    this.order = this.settings.order === "random" ? shuffle(this.words.map((_, i) => i)) : [];
    if (startIndex !== null) {
      this.index = Math.max(0, Math.min(this.words.length - 1, startIndex));
      // 随机模式下把起点从队列里剔除，避免紧接着又抽到它
      this.order = this.order.filter((i) => i !== this.index);
    } else if (this.settings.order === "random" && this.order.length) {
      // 随机模式：把第一个词从队列里取出来作为起点，避免它被再抽一次
      this.index = this.order.shift();
    }
    if (!this.words.length) {
      this.status = "idle";
      this._emit();
      return false;
    }
    this.status = "playing";
    this.phase = "playing";
    this._clearTimer();
    this._step();
    return true;
  }

  /**
   * 循环"单个单词"（点词触发）。
   * words 仅用于 prev/next 与"自动下一个"，当前词必定从第 1 次开始。
   */
  startSingle(word, words = []) {
    if (!word) return false;
    const list = Array.isArray(words) && words.length ? words.slice() : [word];
    let index = list.findIndex((item) => item.id === word.id);
    if (index < 0) {
      list.unshift(word);
      index = 0;
    }
    return this.start(list, index, true);
  }

  /** 当前是否正在循环这个单词（含间隔等待阶段） */
  isLoopingWord(wordId) {
    if (this.status === "idle") return false;
    const word = (this.current && this.current.word) || this.words[this.index];
    return Boolean(word && word.id === wordId);
  }

  pause() {
    if (this.status === "idle" || this.status === "paused") return;
    this._clearTimer();
    if (this.clipActive) this.pauseClip();
    this.status = "paused";
    this._emit();
  }

  async resume() {
    if (this.status !== "paused") return;
    this.status = "playing";
    if (this.clipActive) {
      const ok = await this.resumeClip();
      if (!ok) this._step();          // 资源已结束/被回收：直接推进
    } else if (this.phase === "waiting") {
      this._scheduleInterval(false);  // 恢复间隔计时
    } else {
      this._step();
    }
    this._emit();
  }

  stop() {
    this.runId += 1;
    this._clearTimer();
    try { this.stopClip(); } catch (_) { /* ignore */ }
    this.status = "idle";
    this.phase = "idle";
    this.clipActive = false;
    this.current = null;
    this.singleWord = false;
    this.index = 0;
    this.rep = 0;
    this.accentPos = 0;
    this._emit();
  }

  next() { this._skipTo(1); }

  prev() { this._skipTo(-1); }

  _skipTo(delta) {
    if (!this.words.length) return;
    const wasPaused = this.status === "paused";
    this.runId += 1;                  // 作废当前 await
    this._clearTimer();
    try { this.stopClip(); } catch (_) { /* ignore */ }
    this.clipActive = false;

    const total = this.words.length;
    if (delta > 0 && this.settings.order === "random") {
      if (!this.order.length) {
        this.order = shuffle(this.words.map((_, i) => i).filter((i) => i !== this.index));
      }
      this.index = this.order.length ? this.order.shift() : (this.index + 1) % total;
    } else {
      this.index = (this.index + delta + total) % total;
    }
    this.rep = 0;
    this.accentPos = 0;
    this.current = null;
    this.status = wasPaused ? "paused" : "playing";
    this.phase = "playing";
    if (wasPaused) this._emit();
    else this._step();
  }

  _accents(word) {
    const wanted = this.settings.accents === "both" ? ["uk", "us"] : [this.settings.accents];
    return wanted.filter((accent) => {
      const status = word[`${accent}_audio_status`];
      if (status) return status !== "missing";      // unknown 也尝试一次
      return word[`has_${accent}_audio`] !== false;
    });
  }

  /** 取出下一段音频；返回 null 表示本次播放链结束（已播完/已停止） */
  _takeNextClip() {
    const total = this.words.length;
    for (let guard = 0; guard <= total; guard += 1) {
      if (this.status === "idle") return null;
      const word = this.words[this.index];
      if (!word) { this._finishList(); return null; }
      if (this.rep >= this.settings.repeat) {
        if (!this._advanceWord()) return null;
        continue;
      }
      const accents = this._accents(word);
      if (!accents.length) {
        this.skipped += 1;
        this.lastError = `“${word.lemma || word.display}” 没有可用发音，已跳过`;
        if (!this._advanceWord()) return null;
        continue;
      }
      const accent = accents[Math.min(this.accentPos, accents.length - 1)];
      const displayRep = this.rep + 1;
      this.accentPos += 1;
      if (this.accentPos >= accents.length) {
        this.accentPos = 0;
        this.rep += 1;
      }
      return { word, accent, displayRep };
    }
    this.lastError = "播放列表中没有可用发音的单词";
    this._finishList();
    return null;
  }

  _advanceWord() {
    this.rep = 0;
    this.accentPos = 0;
    // 点词触发的单词语义：默认播完就停；开启"自动下一个"才继续列表
    if (this.singleWord && !this.settings.autoNext) {
      this._finishList();
      return false;
    }
    if (this.settings.order === "random") {
      if (!this.order.length) {
        if (!this.settings.loopList && this.played > 0) { this._finishList(); return false; }
        this.order = shuffle(this.words.map((_, i) => i));
      }
      this.index = this.order.shift();
    } else {
      this.index += 1;
      if (this.index >= this.words.length) {
        if (!this.settings.loopList) { this._finishList(); return false; }
        this.index = 0;
      }
    }
    return true;
  }

  _finishList() {
    this._clearTimer();
    this.current = null;
    this.status = "idle";
    this.phase = "idle";
    this.clipActive = false;
    this._emit("completed");
  }

  async _step() {
    if (this.status !== "playing") return;
    const runId = ++this.runId;
    const clip = this._takeNextClip();
    if (!clip || this.status === "idle") return;

    this.current = clip;
    this.clipActive = true;
    this.phase = "playing";
    this._emit();

    let failed = false;
    try {
      await this.playClip(clip.word, clip.accent);
    } catch (err) {
      if (this.runId !== runId) return;                // 被停止/切换取代
      const reason = String((err && err.message) || err);
      if (reason === "stopped" || reason === "superseded") return;
      failed = true;
      this.lastError = `“${clip.word.lemma || clip.word.display}” 播放失败：${reason}`;
    }
    this.clipActive = false;
    if (this.runId !== runId) return;
    if (this.status === "idle") return;
    if (this.status === "paused") { this._emit(); return; }

    this.played += 1;
    this._scheduleInterval(failed);
  }

  _scheduleInterval(failed = false) {
    this._clearTimer();
    this.phase = "waiting";
    this._emit();
    const waitMs = failed ? Math.max(500, this.settings.interval * 1000) : this.settings.interval * 1000;
    this.timer = this.setTimeoutFn(() => {
      this.timer = null;
      if (this.status !== "playing") return;
      this.phase = "playing";
      this._step();
    }, waitMs);
  }

  _clearTimer() {
    if (this.timer !== null) {
      this.clearTimeoutFn(this.timer);
      this.timer = null;
    }
  }

  _emit(event) {
    this.onUpdate(this.snapshot(), event);
  }
}
