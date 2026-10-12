// 浏览器端存储（IndexedDB）：替代本地 Python 版的 SQLite。
// 表结构/字段与后端 API 返回保持一致，所以页面代码无需改动。

const DB_NAME = "wordbook";
const DB_VERSION = 1;
const STORES = {
  books: { keyPath: "id", autoIncrement: true },
  chapters: { keyPath: "id", autoIncrement: true, indexes: [["book_id", "book_id"]] },
  words: { keyPath: "id", autoIncrement: true, indexes: [["book_id", "book_id"], ["book_id_lemma", ["book_id", "lemma"], { unique: true }]] },
  links: { keyPath: "id", autoIncrement: true, indexes: [["chapter_id", "chapter_id"], ["word_id", "word_id"]] },
};

let dbPromise = null;

function openDb() {
  if (dbPromise) return dbPromise;
  dbPromise = new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);
    request.onupgradeneeded = () => {
      const db = request.result;
      for (const [name, spec] of Object.entries(STORES)) {
        if (db.objectStoreNames.contains(name)) continue;
        const store = db.createObjectStore(name, { keyPath: spec.keyPath, autoIncrement: spec.autoIncrement });
        for (const [indexName, keyPath, options] of spec.indexes || []) {
          store.createIndex(indexName, keyPath, options || {});
        }
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
  return dbPromise;
}

function tx(db, names, mode) {
  return db.transaction(names, mode);
}

function requestToPromise(request) {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function withStore(name, mode, fn) {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const transaction = tx(db, [name], mode);
    let result;
    transaction.oncomplete = () => resolve(result);
    transaction.onerror = () => reject(transaction.error);
    transaction.onabort = () => reject(transaction.error);
    Promise.resolve(fn(transaction.objectStore(name))).then((value) => { result = value; }).catch(reject);
  });
}

async function withStores(names, mode, fn) {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const transaction = tx(db, names, mode);
    let result;
    transaction.oncomplete = () => resolve(result);
    transaction.onerror = () => reject(transaction.error);
    transaction.onabort = () => reject(transaction.error);
    Promise.resolve(fn(...names.map((name) => transaction.objectStore(name)))).then((value) => { result = value; }).catch(reject);
  });
}

const now = () => new Date().toISOString().slice(0, 19).replace("T", " ");

// ---------------------------------------------------------------- books
export async function createBook(fields) {
  const book = { created_at: now(), status: "parsing", ...fields };
  delete book.id;
  const id = await withStore("books", "readwrite", (store) => requestToPromise(store.add(book)));
  return { ...book, id };
}

export async function updateBook(id, patch) {
  return withStore("books", "readwrite", async (store) => {
    const book = await requestToPromise(store.get(id));
    if (!book) throw new Error("单词书不存在");
    const next = { ...book, ...patch };
    await requestToPromise(store.put(next));
    return next;
  });
}

export async function getBook(id) {
  return withStore("books", "readonly", (store) => requestToPromise(store.get(id)));
}

export async function listBooks() {
  const books = await withStore("books", "readonly", (store) => requestToPromise(store.getAll()));
  return books.sort((a, b) => b.id - a.id);
}

/**
 * 统计计数直接存在书的记录上（首页只需一次读取，不用把几千个词条全读一遍）。
 * counters: { chapter_count, word_count, learned_count, mastered_count, favorite_count }
 */
export async function updateBookCounters(bookId, counters) {
  return withStore("books", "readwrite", async (store) => {
    const book = await requestToPromise(store.get(bookId));
    if (!book) return null;
    const next = { ...book, ...counters };
    await requestToPromise(store.put(next));
    return next;
  });
}

export async function adjustBookCounters(bookId, delta) {
  return withStore("books", "readwrite", async (store) => {
    const book = await requestToPromise(store.get(bookId));
    if (!book) return null;
    const next = { ...book };
    for (const [key, value] of Object.entries(delta)) {
      next[key] = (Number(next[key]) || 0) + value;
    }
    await requestToPromise(store.put(next));
    return next;
  });
}

export async function deleteBook(id) {
  await withStores(["books", "chapters", "words", "links"], "readwrite", async (books, chapters, words, links) => {
    await requestToPromise(books.delete(id));
    const chapterRows = await requestToPromise(chapters.index("book_id").getAll(id));
    for (const chapter of chapterRows) {
      const rows = await requestToPromise(links.index("chapter_id").getAll(chapter.id));
      for (const row of rows) await requestToPromise(links.delete(row.id));
      await requestToPromise(chapters.delete(chapter.id));
    }
    const wordRows = await requestToPromise(words.index("book_id").getAll(id));
    for (const word of wordRows) await requestToPromise(words.delete(word.id));
  });
}

// ---------------------------------------------------------------- chapters
export async function replaceChapters(bookId, chapterList) {
  return withStores(["chapters", "links"], "readwrite", async (chapters, links) => {
    const existing = await requestToPromise(chapters.index("book_id").getAll(bookId));
    for (const chapter of existing) {
      const rows = await requestToPromise(links.index("chapter_id").getAll(chapter.id));
      for (const row of rows) await requestToPromise(links.delete(row.id));
      await requestToPromise(chapters.delete(chapter.id));
    }
    const created = [];
    for (let index = 0; index < chapterList.length; index += 1) {
      const chapter = chapterList[index];
      const record = {
        book_id: bookId,
        position: index,
        title: chapter.title,
        start_page: Number(chapter.start_page),
        end_page: Number(chapter.end_page),
        source: chapter.source || "manual",
        number: chapter.number ?? null,
        confidence: chapter.confidence || "unknown",
        evidence: chapter.evidence || [],
        alerts: chapter.alerts || [],
        extraction_mode: chapter.extraction_mode || null,
      };
      const id = await requestToPromise(chapters.add(record));
      created.push({ ...record, id });
    }
    return created;
  });
}

export async function listChapters(bookId) {
  const chapters = await withStores(["chapters", "links", "words"], "readonly",
    async (chapterStore, linkStore, wordStore) => {
      const rows = await requestToPromise(chapterStore.index("book_id").getAll(bookId));
      const sorted = rows.sort((a, b) => a.position - b.position);
      // 关键：先一次性把所有请求都发出去（同一事务内并发执行），再统一等待。
      // 之前是逐个 await，一本书 3000+ 个词在手机上会卡很多秒。
      const linkSets = await Promise.all(
        sorted.map((chapter) => requestToPromise(linkStore.index("chapter_id").getAll(chapter.id))),
      );
      const wordIds = new Set();
      for (const links of linkSets) for (const link of links) wordIds.add(link.word_id);
      const wordRows = await Promise.all(
        [...wordIds].map((id) => requestToPromise(wordStore.get(id))),
      );
      const byId = new Map(wordRows.filter(Boolean).map((word) => [word.id, word]));
      return sorted.map((chapter, index) => {
        const links = linkSets[index];
        let mastered = 0;
        let learned = 0;
        for (const link of links) {
          const word = byId.get(link.word_id);
          if (!word) continue;
          if (word.mastered) mastered += 1;
          if (word.meaning_cn || word.uk_ipa) learned += 1;
        }
        return { ...chapter, word_count: links.length, mastered_count: mastered, learned_count: learned };
      });
    });
  return chapters;
}

export async function getChapterRow(chapterId) {
  return withStore("chapters", "readonly", (store) => requestToPromise(store.get(chapterId)));
}

export async function updateChapter(chapterId, patch) {
  return withStore("chapters", "readwrite", async (store) => {
    const row = await requestToPromise(store.get(chapterId));
    if (!row) throw new Error("章节不存在");
    const next = { ...row, ...patch };
    await requestToPromise(store.put(next));
    return next;
  });
}

// ---------------------------------------------------------------- words
export function wordApi(word, extra = {}) {
  if (!word) return null;
  const status = (accent) => {
    if (word[`${accent}_audio_source`] && word[`${accent}_audio_verified`]) return "available";
    if (word[`${accent}_audio_missing`]) return "missing";
    return "unknown";
  };
  return {
    ...word,
    in_dict: Boolean(word.in_dict),
    favorite: Boolean(word.favorite),
    mastered: Boolean(word.mastered),
    uk_audio_status: status("uk"),
    us_audio_status: status("us"),
    has_uk_audio: status("uk") === "available",
    has_us_audio: status("us") === "available",
    ...extra,
  };
}

export async function upsertWord(bookId, fields) {
  return withStore("words", "readwrite", async (store) => {
    const index = store.index("book_id_lemma");
    let row = await requestToPromise(index.get([bookId, fields.lemma]));
    if (row) {
      row = { ...row, ...fields };
      await requestToPromise(store.put(row));
      return row;
    }
    const record = { book_id: bookId, in_dict: 0, favorite: 0, mastered: 0, created_at: now(), ...fields };
    delete record.id;
    const id = await requestToPromise(store.add(record));
    return { ...record, id };
  });
}

export async function getWord(id) {
  return withStore("words", "readonly", (store) => requestToPromise(store.get(id)));
}

export async function updateWord(id, patch) {
  return withStore("words", "readwrite", async (store) => {
    const row = await requestToPromise(store.get(id));
    if (!row) throw new Error("词条不存在");
    const next = { ...row, ...patch };
    await requestToPromise(store.put(next));
    return next;
  });
}

export async function replaceLinks(chapterId, entries) {
  return withStores(["links", "words"], "readwrite", async (links, words) => {
    const existing = await requestToPromise(links.index("chapter_id").getAll(chapterId));
    for (const row of existing) await requestToPromise(links.delete(row.id));
    for (const entry of entries) {
      await requestToPromise(links.add({
        chapter_id: chapterId,
        word_id: entry.word_id,
        count: entry.count || 1,
        pages: entry.pages || [],
        forms: entry.forms || {},
        ordinal: entry.ordinal || 0,
      }));
    }
  });
}

export async function getChapterWords(chapterId) {
  return withStores(["links", "words"], "readonly", async (links, words) => {
    const rows = await requestToPromise(links.index("chapter_id").getAll(chapterId));
    rows.sort((a, b) => (a.ordinal || 0) - (b.ordinal || 0) || (b.count || 0) - (a.count || 0));
    const wordRows = await Promise.all(rows.map((link) => requestToPromise(words.get(link.word_id))));
    return rows.map((link, index) => {
      const word = wordRows[index];
      if (!word) return null;
      return wordApi(word, {
        chapter_count: link.count,
        chapter_pages: link.pages,
        chapter_forms: link.forms,
        chapter_ordinal: link.ordinal,
      });
    }).filter(Boolean);
  });
}

export async function listBookWords(bookId) {
  return withStores(["links", "chapters", "words"], "readonly", async (links, chapters, words) => {
    const chapterRows = await requestToPromise(chapters.index("book_id").getAll(bookId));
    const linkSets = await Promise.all(
      chapterRows.map((chapter) => requestToPromise(links.index("chapter_id").getAll(chapter.id))),
    );
    const wordIds = [];
    const seen = new Set();
    for (const rows of linkSets) {
      for (const link of rows) {
        if (seen.has(link.word_id)) continue;
        seen.add(link.word_id);
        wordIds.push(link.word_id);
      }
    }
    const wordRows = await Promise.all(wordIds.map((id) => requestToPromise(words.get(id))));
    return wordRows.filter(Boolean).map((word) => wordApi(word))
      .sort((a, b) => a.lemma.localeCompare(b.lemma));
  });
}

export async function addWordToChapter(bookId, chapterId, word) {
  const lemma = word.trim().toLowerCase();
  const row = await upsertWord(bookId, { lemma, display: lemma, in_dict: 0 });
  const existing = await withStore("links", "readonly",
    (store) => requestToPromise(store.index("chapter_id").getAll(chapterId)));
  if (!existing.some((link) => link.word_id === row.id)) {
    await withStore("links", "readwrite", (store) => requestToPromise(
      store.add({ chapter_id: chapterId, word_id: row.id, count: 0, pages: [], forms: {}, ordinal: 0 }),
    ));
  }
  return wordApi(row);
}

export async function removeWordFromChapter(chapterId, wordId) {
  await withStore("links", "readwrite", async (store) => {
    const rows = await requestToPromise(store.index("chapter_id").getAll(chapterId));
    for (const row of rows) {
      if (row.word_id === wordId) await requestToPromise(store.delete(row.id));
    }
  });
}

export async function setAudioStatus(wordId, accent, { available, missing, provider, duration }) {
  return withStore("words", "readwrite", async (store) => {
    const row = await requestToPromise(store.get(wordId));
    if (!row) return null;
    const next = { ...row };
    if (available) {
      next[`${accent}_audio_source`] = provider || "remote";
      next[`${accent}_audio_verified`] = 1;
      next[`${accent}_audio_missing`] = 0;
    } else if (missing) {
      next[`${accent}_audio_missing`] = 1;
    }
    next.audio_checked_at = now();
    await requestToPromise(store.put(next));
    return next;
  });
}

export async function resetAll() {
  const db = await openDb();
  const names = Object.keys(STORES);
  await new Promise((resolve, reject) => {
    const transaction = tx(db, names, "readwrite");
    for (const name of names) transaction.objectStore(name).clear();
    transaction.oncomplete = resolve;
    transaction.onerror = () => reject(transaction.error);
  });
}
