const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

function getNested(obj, dotPath) {
  if (!obj || typeof obj !== 'object') return undefined;
  if (Object.prototype.hasOwnProperty.call(obj, dotPath)) return obj[dotPath];
  const parts = String(dotPath).split('.');
  let cur = obj;
  for (const p of parts) {
    if (cur == null || typeof cur !== 'object') return undefined;
    cur = cur[p];
  }
  return cur;
}

function setNested(obj, dotPath, val) {
  const parts = String(dotPath).split('.');
  let cur = obj;
  for (let i = 0; i < parts.length - 1; i++) {
    const p = parts[i];
    if (!cur[p] || typeof cur[p] !== 'object') cur[p] = {};
    cur = cur[p];
  }
  cur[parts[parts.length - 1]] = val;
}

function valuesEqual(a, b) {
  if (a === b) return true;
  if (a == null || b == null) return false;
  if (typeof a === 'object' && typeof b === 'object') {
    return String(a) === String(b);
  }
  return String(a) === String(b);
}

function matchCondition(docVal, cond) {
  if (cond instanceof RegExp) {
    if (Array.isArray(docVal)) return docVal.some(v => cond.test(String(v)));
    return cond.test(String(docVal ?? ''));
  }
  if (cond && typeof cond === 'object' && !Array.isArray(cond) && !(cond instanceof Date)) {
    const ops = Object.keys(cond);
    const hasOp = ops.some(k => k.startsWith('$'));
    if (hasOp) {
      for (const op of ops) {
        if (op === '$exists') {
          const exists = docVal !== undefined;
          if (Boolean(cond.$exists) !== exists) return false;
        } else if (op === '$ne') {
          if (cond.$ne === null) {
            if (docVal === null || docVal === undefined) return false;
          } else if (valuesEqual(docVal, cond.$ne)) {
            return false;
          }
        } else if (op === '$in') {
          const arr = Array.isArray(cond.$in) ? cond.$in : [];
          if (Array.isArray(docVal)) {
            if (!docVal.some(dv => arr.some(iv => valuesEqual(dv, iv)))) return false;
          } else {
            if (!arr.some(iv => valuesEqual(docVal, iv))) return false;
          }
        } else if (op === '$nin') {
          const arr = Array.isArray(cond.$nin) ? cond.$nin : [];
          if (Array.isArray(docVal)) {
            if (docVal.some(dv => arr.some(iv => valuesEqual(dv, iv)))) return false;
          } else {
            if (arr.some(iv => valuesEqual(docVal, iv))) return false;
          }
        } else if (op === '$regex') {
          const rx = cond.$regex instanceof RegExp
            ? cond.$regex
            : new RegExp(String(cond.$regex), cond.$options || '');
          if (Array.isArray(docVal)) {
            if (!docVal.some(v => rx.test(String(v)))) return false;
          } else {
            if (!rx.test(String(docVal ?? ''))) return false;
          }
        } else if (op === '$options') {
          // Handled alongside $regex
        } else if (op === '$gt') {
          if (!(docVal > cond.$gt)) return false;
        } else if (op === '$gte') {
          if (!(docVal >= cond.$gte)) return false;
        } else if (op === '$lt') {
          if (!(docVal < cond.$lt)) return false;
        } else if (op === '$lte') {
          if (!(docVal <= cond.$lte)) return false;
        }
      }
      return true;
    }
  }
  if (Array.isArray(docVal)) {
    return docVal.some(v => valuesEqual(v, cond));
  }
  return valuesEqual(docVal, cond);
}

function matchFilter(doc, filter) {
  if (!filter || typeof filter !== 'object' || Object.keys(filter).length === 0) return true;
  for (const [key, val] of Object.entries(filter)) {
    if (key === '$or') {
      if (!Array.isArray(val) || !val.some(sub => matchFilter(doc, sub))) return false;
    } else if (key === '$and') {
      if (!Array.isArray(val) || !val.every(sub => matchFilter(doc, sub))) return false;
    } else if (key === '$nor') {
      if (Array.isArray(val) && val.some(sub => matchFilter(doc, sub))) return false;
    } else {
      const docVal = getNested(doc, key);
      if (!matchCondition(docVal, val)) return false;
    }
  }
  return true;
}

function applyProjection(doc, proj) {
  if (!proj || typeof proj !== 'object' || Object.keys(proj).length === 0) return doc;
  const entries = Object.entries(proj);
  const inclusions = entries.filter(([k, v]) => v === 1 || v === true);
  if (inclusions.length === 0) {
    const copy = { ...doc };
    for (const [k, v] of entries) {
      if (v === 0 || v === false) delete copy[k];
    }
    return copy;
  }
  const out = {};
  if (doc._id !== undefined && proj._id !== 0 && proj._id !== false) out._id = doc._id;
  for (const [k] of inclusions) {
    if (Object.prototype.hasOwnProperty.call(doc, k)) out[k] = doc[k];
  }
  return out;
}

function applyUpdate(doc, update, isInsert) {
  if (!update || typeof update !== 'object') return;
  if (update.$set && typeof update.$set === 'object') {
    for (const [k, v] of Object.entries(update.$set)) {
      if (v === undefined) continue;
      if (k.includes('.')) setNested(doc, k, v);
      else doc[k] = v;
    }
  }
  if (isInsert && update.$setOnInsert && typeof update.$setOnInsert === 'object') {
    for (const [k, v] of Object.entries(update.$setOnInsert)) {
      if (k.includes('.')) setNested(doc, k, v);
      else doc[k] = v;
    }
  }
  if (update.$inc && typeof update.$inc === 'object') {
    for (const [k, v] of Object.entries(update.$inc)) {
      const cur = Number(getNested(doc, k) || 0);
      if (k.includes('.')) setNested(doc, k, cur + Number(v || 0));
      else doc[k] = cur + Number(v || 0);
    }
  }
}

function loadInitialCollections() {
  const memStore = new Map();
  const candidates = [
    path.join(process.cwd(), 'db-fallback.json'),
    path.join(__dirname, '..', 'db-fallback.json')
  ];
  for (const p of candidates) {
    try {
      if (fs.existsSync(p)) {
        const raw = JSON.parse(fs.readFileSync(p, 'utf8'));
        const cols = raw && raw.collections ? raw.collections : {};
        for (const [name, docs] of Object.entries(cols)) {
          if (Array.isArray(docs)) {
            memStore.set(name, JSON.parse(JSON.stringify(docs)));
          }
        }
        break;
      }
    } catch (_) {}
  }
  return memStore;
}

function createMockDb(seedCollections = null) {
  const memStore = seedCollections instanceof Map ? seedCollections : loadInitialCollections();

  const getCol = (name) => {
    if (!memStore.has(name)) memStore.set(name, []);
    const items = memStore.get(name);

    const makeCursor = (filter) => {
      let _proj = null;
      let _sort = null;
      let _skip = 0;
      let _limit = 0;

      const exec = () => {
        let res = items.filter(d => matchFilter(d, filter));
        if (_sort && typeof _sort === 'object') {
          const sortEntries = Object.entries(_sort);
          res = res.slice().sort((a, b) => {
            for (const [field, dir] of sortEntries) {
              const av = getNested(a, field);
              const bv = getNested(b, field);
              if (av === bv) continue;
              if (av == null) return dir >= 0 ? -1 : 1;
              if (bv == null) return dir >= 0 ? 1 : -1;
              const at = av instanceof Date ? av.getTime() : av;
              const bt = bv instanceof Date ? bv.getTime() : bv;
              if (at < bt) return dir >= 0 ? -1 : 1;
              if (at > bt) return dir >= 0 ? 1 : -1;
            }
            return 0;
          });
        }
        if (_skip > 0) res = res.slice(_skip);
        if (_limit > 0) res = res.slice(0, _limit);
        if (_proj) res = res.map(d => applyProjection(d, _proj));
        return res;
      };

      const cursor = {
        project(p) { _proj = p; return cursor; },
        sort(s) { _sort = s; return cursor; },
        skip(n) { _skip = Number(n) || 0; return cursor; },
        limit(n) { _limit = Number(n) || 0; return cursor; },
        async toArray() { return exec(); },
        async next() { const arr = exec(); return arr[0] || null; }
      };
      return cursor;
    };

    return {
      find(filter = {}) {
        return makeCursor(filter);
      },
      async findOne(filter = {}, opts = {}) {
        const doc = items.find(d => matchFilter(d, filter)) || null;
        if (!doc) return null;
        return opts && opts.projection ? applyProjection(doc, opts.projection) : doc;
      },
      async insertOne(doc) {
        const copy = { _id: doc._id || crypto.randomBytes(12).toString('hex'), ...doc };
        items.push(copy);
        return { acknowledged: true, insertedId: copy._id };
      },
      async insertMany(docs) {
        const insertedIds = {};
        (docs || []).forEach((d, idx) => {
          const copy = { _id: d._id || crypto.randomBytes(12).toString('hex'), ...d };
          items.push(copy);
          insertedIds[idx] = copy._id;
        });
        return { acknowledged: true, insertedCount: (docs || []).length, insertedIds };
      },
      async updateOne(filter, update, opts = {}) {
        const doc = items.find(d => matchFilter(d, filter));
        if (doc) {
          applyUpdate(doc, update, false);
          return { acknowledged: true, matchedCount: 1, modifiedCount: 1 };
        }
        if (opts && opts.upsert) {
          const newDoc = { _id: (filter && filter._id) || crypto.randomBytes(12).toString('hex') };
          if (filter && typeof filter === 'object') {
            for (const [k, v] of Object.entries(filter)) {
              if (!k.startsWith('$') && (typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean')) {
                setNested(newDoc, k, v);
              }
            }
          }
          applyUpdate(newDoc, update, true);
          items.push(newDoc);
          return { acknowledged: true, matchedCount: 0, modifiedCount: 0, upsertedCount: 1, upsertedId: newDoc._id };
        }
        return { acknowledged: true, matchedCount: 0, modifiedCount: 0 };
      },
      async updateMany(filter, update, opts = {}) {
        const matched = items.filter(d => matchFilter(d, filter));
        for (const doc of matched) applyUpdate(doc, update, false);
        if (matched.length === 0 && opts && opts.upsert) {
          await this.updateOne(filter, update, opts);
        }
        return { acknowledged: true, matchedCount: matched.length, modifiedCount: matched.length };
      },
      async findOneAndUpdate(filter, update, opts = {}) {
        let doc = items.find(d => matchFilter(d, filter));
        if (!doc && opts && opts.upsert) {
          await this.updateOne(filter, update, opts);
          doc = items.find(d => matchFilter(d, filter)) || null;
          return { value: doc, ...(doc || {}) };
        }
        if (!doc) return { value: null };
        applyUpdate(doc, update, false);
        return { value: doc, ...doc };
      },
      async deleteOne(filter = {}) {
        const idx = items.findIndex(d => matchFilter(d, filter));
        if (idx >= 0) {
          items.splice(idx, 1);
          return { acknowledged: true, deletedCount: 1 };
        }
        return { acknowledged: true, deletedCount: 0 };
      },
      async deleteMany(filter = {}) {
        const before = items.length;
        const kept = items.filter(d => !matchFilter(d, filter));
        items.length = 0;
        items.push(...kept);
        return { acknowledged: true, deletedCount: before - items.length };
      },
      async countDocuments(filter = {}) {
        if (!filter || Object.keys(filter).length === 0) return items.length;
        return items.filter(d => matchFilter(d, filter)).length;
      },
      async createIndex() { return 'ok'; }
    };
  };

  return {
    collection: (name) => getCol(name),
    listCollections: () => ({
      toArray: async () => Array.from(memStore.keys()).map(name => ({ name }))
    }),
    command: async () => ({ ok: 1 })
  };
}

let sharedInstance = null;
function getSharedMockDb() {
  if (!sharedInstance) sharedInstance = createMockDb();
  return sharedInstance;
}

module.exports = {
  createMockDb,
  getSharedMockDb,
  matchFilter,
  getNested
};
