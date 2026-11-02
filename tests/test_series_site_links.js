const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const path = require('path');

const code = fs.readFileSync(path.join(__dirname, '../detail/script.js'), 'utf8');
const providersStart = code.indexOf('  const siteNames = ');
const providersEnd = code.indexOf('  function siteFavicon(', providersStart);
assert.ok(providersStart >= 0 && providersEnd > providersStart,
  'provider identity and series shortcut helpers must be present');
assert.ok(code.slice(providersStart, providersEnd).includes('function seriesSiteLinks('),
  'series shortcuts must select representative per-file links');

const safeStart = code.indexOf('  function safeUrl(');
const safeEnd = code.indexOf('\n  // The grid remains mounted', safeStart);
assert.ok(safeStart >= 0 && safeEnd > safeStart, 'URL validation must be present');
const context = {
  URL,
  location: { origin: 'https://oasis.example' },
  compareSeriesVolumeBooks: (a, b) => {
    const left = Number(a.volume || a.document_volume_index || 0);
    const right = Number(b.volume || b.document_volume_index || 0);
    return left > 0 && right > 0 ? left - right : left > 0 ? -1 : right > 0 ? 1 : 0;
  },
  chapterNumber: (book) => Number(book.number || book.chapter_number || 0),
};
vm.createContext(context);
vm.runInContext(code.slice(safeStart, safeEnd) + code.slice(providersStart, providersEnd)
  + '\nthis.seriesSiteLinks = seriesSiteLinks; this.siteInfo = siteInfo;', context);

const linksFor = (books, metadata = '') => Array.from(context.seriesSiteLinks(books, metadata));
const perSite = (links, key) => links.filter((link) => context.siteInfo(link).key === key);

// Registration order, browser reverse order and aggregated series metadata can
// all begin with a later volume. The shortcut must still open volume 1.
const ridi1 = 'https://ridibooks.com/books/900000001';
const ridi4 = 'https://ridibooks.com/books/100000004';
const lateFirstVolume = [
  { id: 10, volume: 4, link: ridi4 },
  { id: 99, volume: 1, link: ridi1 },
];
const untouched = JSON.stringify(lateFirstVolume);
assert.deepStrictEqual(linksFor(lateFirstVolume, ridi4 + ', ' + ridi1), [ridi1],
  'a late-registered first volume must beat latest-volume metadata links');
assert.deepStrictEqual(linksFor([...lateFirstVolume].reverse(), ridi4), [ridi1],
  'carousel reverse order must not change the representative shortcut');
assert.strictEqual(JSON.stringify(lateFirstVolume), untouched, 'book rows must not be mutated');

const kyobo1 = 'https://ebook-product.kyobobook.co.kr/dig/epd/ebook/E000003568417';
const kyobo4 = 'https://product.kyobobook.co.kr/detail/S000000000004';
const codeMatched = linksFor([
  { volume: 4, file_path: '/books/작품 04권 (S000000000004).epub', link: kyobo4 },
  { volume: 1, file_path: '/books/작품 01권 (E000003568417).epub',
    link: kyobo4 + '\n' + kyobo1 },
], kyobo4);
assert.deepStrictEqual(codeMatched, [kyobo1],
  'the volume 1 E-code must choose its matching Kyobo URL instead of the smaller product number');
const sMatched = 'https://product.kyobobook.co.kr/detail/S000003568417';
assert.deepStrictEqual(linksFor([
  { volume: 1, file_path: '/books/작품 (S000003568417).epub',
    link: kyobo4 + ', ' + sMatched },
]), [sMatched], 'S-codes must also select the matching product');
const prefixOnly = 'https://ebook-product.kyobobook.co.kr/dig/epd/ebook/E0000035684179';
assert.deepStrictEqual(linksFor([
  { volume: 1, file_path: '/books/작품 (E000003568417).epub',
    link: prefixOnly + ', ' + kyobo1 },
]), [kyobo1], 'a longer product code must not count as an exact code match');

assert.deepStrictEqual(linksFor([
  { document_volume_index: 100000, file_path: '/books/단편.epub', link: ridi1 },
], ridi4), [ridi1], 'standalone volume sentinels must retain their own file link');
assert.deepStrictEqual(linksFor([
  { number: 4, link: ridi4 }, { number: 1, link: ridi1 },
]), [ridi1], 'chapter-only works must choose the first chapter independently of input order');

const providerUrls = [
  ridi1,
  'https://ridi.com/books/900000002',
  kyobo1,
  kyobo4,
  'https://www.yes24.com/Product/Goods/101',
  'https://m.yes24.com/Goods/Detail/102',
  'https://series.naver.com/comic/detail.series?productNo=201',
  'https://comic.naver.com/webtoon/list?titleId=202',
  'https://page.kakao.com/content/301',
  'https://webtoon.kakao.com/content/302',
  'https://shop-one.example/books/401',
  'https://shop-two.example/books/402',
];
const providers = linksFor([{ volume: 1, link: providerUrls.join('\n') }]);
assert.strictEqual(perSite(providers, 'ridibooks.com').length, 1,
  'Ridi domain aliases must share one shortcut');
assert.strictEqual(perSite(providers, 'kyobobook.co.kr').length, 1,
  'Kyobo subdomains must share one shortcut');
assert.strictEqual(perSite(providers, 'yes24.com').length, 1,
  'YES24 mobile and desktop domains must share one shortcut');
for (const host of ['series.naver.com', 'comic.naver.com', 'page.kakao.com', 'webtoon.kakao.com',
  'shop-one.example', 'shop-two.example']) {
  assert.strictEqual(perSite(providers, host).length, 1,
    host + ' must retain its independent shortcut');
}
assert.strictEqual(providers.length, 9, 'known aliases collapse without merging separate platforms or unknown hosts');

const yes24 = 'https://www.yes24.com/Product/Goods/101';
assert.deepStrictEqual(linksFor([{ volume: 1, link: ridi1 }, { volume: 2, link: yes24 }],
  kyobo1 + ', ' + ridi4), [ridi1, yes24, kyobo1],
  'missing sites must be filled from later files and finally series metadata');
assert.deepStrictEqual(linksFor([], ridi1 + ', ' + ridi4 + '\n' + yes24), [ridi1, yes24],
  'series metadata alone must still supply one safe link per site');
assert.deepStrictEqual(linksFor([
  { volume: 1, link: 'javascript:alert(1)\nftp://files.example/book\n' + ridi1 },
], 'javascript:alert(2)\n' + yes24), [ridi1, yes24],
  'unsafe protocols must never become shortcuts');

console.log('PASS representative volume/chapter links, exact E/S codes, provider identities, fallback and safe URLs');
