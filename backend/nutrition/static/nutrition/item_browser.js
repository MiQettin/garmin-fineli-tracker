// Browsing a long list of own foods (nutrition/includes/item_browser_toolbar.html):
// search, sort, cards or a compact list, and A-Ö letters to jump with.
//
// The page renders every item twice, as a card in [data-browser-cards] and
// as a row in [data-browser-rows], both with the same data-item and data-*
// sort values. Sort and view are remembered per page in localStorage.
(function () {
    const COMPARE = {
        name: (a, b) => a.dataset.name.localeCompare(b.dataset.name, 'fi', {sensitivity: 'base'}),
        uses: (a, b) => num(b, 'uses') - num(a, 'uses'),
        updated: (a, b) => (b.dataset.updated || '').localeCompare(a.dataset.updated || ''),
    };
    // Lists longer than this open as a list rather than cards.
    const LIST_BY_DEFAULT_OVER = 12;

    function num(item, key) {
        const value = parseFloat(item.dataset[key]);
        return Number.isNaN(value) ? 0 : value;
    }

    function letterOf(item) {
        const first = item.dataset.name.trim().charAt(0).toLocaleUpperCase('fi');
        return /\p{L}/u.test(first) ? first : '#';
    }

    function load(key) {
        try { return localStorage.getItem(key); } catch (e) { return null; }
    }

    function save(key, value) {
        try { localStorage.setItem(key, value); } catch (e) { /* storage unavailable */ }
    }

    function setup(root) {
        const id = root.dataset.browser;
        const search = root.querySelector('[data-browser-search]');
        const sort = root.querySelector('[data-browser-sort]');
        const viewButtons = [...root.querySelectorAll('[data-browser-view]')];
        const count = root.querySelector('[data-browser-count]');
        const letters = root.querySelector('[data-browser-letters]');
        const empty = root.querySelector('[data-browser-empty]');
        const cards = root.querySelector('[data-browser-cards]');
        const rows = root.querySelector('[data-browser-rows]');
        const cardItems = [...cards.querySelectorAll(':scope > [data-item]')];
        const rowItems = [...rows.querySelectorAll(':scope > [data-item]')];
        const total = cardItems.length;

        sort.value = COMPARE[load(`${id}.sort`)] ? load(`${id}.sort`) : 'name';
        let view = load(`${id}.view`) || (total > LIST_BY_DEFAULT_OVER ? 'list' : 'cards');

        function render() {
            const words = search.value.toLocaleLowerCase('fi').split(/\s+/).filter(Boolean);
            const compare = COMPARE[sort.value];
            const byName = sort.value === 'name';
            const listView = view === 'list';
            const container = listView ? rows : cards;
            const items = listView ? rowItems : cardItems;

            cards.hidden = listView;
            rows.hidden = !listView;
            viewButtons.forEach(button => button.setAttribute('aria-pressed', String(button.dataset.browserView === view)));

            container.querySelectorAll('[data-browser-letter]').forEach(heading => heading.remove());
            const shown = [];
            for (const item of [...items].sort((a, b) => compare(a, b) || COMPARE.name(a, b))) {
                const match = words.every(word => item.dataset.search.includes(word));
                item.hidden = !match;
                container.appendChild(item);
                if (match) shown.push(item);
            }

            // A-Ö: a heading per letter (list) and a letter bar to jump with.
            letters.replaceChildren();
            letters.hidden = !byName || shown.length < 2;
            let previous = null;
            for (const item of shown) {
                if (!byName) break;
                const letter = letterOf(item);
                if (letter === previous) continue;
                previous = letter;
                let target = item;
                if (listView) {
                    target = document.createElement('li');
                    target.className = 'browser-letter';
                    target.dataset.browserLetter = letter;
                    target.textContent = letter;
                    container.insertBefore(target, item);
                }
                const jump = document.createElement('button');
                jump.type = 'button';
                jump.className = 'browser-letter-btn';
                jump.textContent = letter;
                jump.addEventListener('click', () => target.scrollIntoView({behavior: 'smooth', block: 'start'}));
                letters.appendChild(jump);
            }

            count.textContent = (shown.length === total ? root.dataset.textCount : root.dataset.textShownOf)
                .replace('{n}', shown.length).replace('{total}', total);
            empty.hidden = shown.length > 0;
        }

        search.addEventListener('input', render);
        sort.addEventListener('change', () => { save(`${id}.sort`, sort.value); render(); });
        viewButtons.forEach(button => button.addEventListener('click', () => {
            view = button.dataset.browserView;
            save(`${id}.view`, view);
            render();
        }));
        render();
    }

    document.querySelectorAll('[data-browser]').forEach(setup);
})();
