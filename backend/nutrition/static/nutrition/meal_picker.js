// Meal name field (nutrition/includes/meal_picker.html): any name can be
// typed; the standard meals are offered as suggestions, filtered by what is
// typed ("Aam" -> Aamiainen). The server maps a standard meal's name to its key.
(function () {
    function setup(combo) {
        const input = combo.querySelector('.meal-combo-input');
        const list = combo.querySelector('.meal-combo-list');
        const toggle = combo.querySelector('.meal-combo-toggle');
        const options = [...list.querySelectorAll('li')];
        let active = -1;

        function visible() {
            return options.filter(option => !option.hidden);
        }

        function setActive(index) {
            const shown = visible();
            options.forEach(option => option.classList.remove('active'));
            active = shown.length ? (index + shown.length) % shown.length : -1;
            if (active >= 0) {
                shown[active].classList.add('active');
                input.setAttribute('aria-activedescendant', shown[active].id);
            } else {
                input.removeAttribute('aria-activedescendant');
            }
        }

        function open(showAll) {
            const typed = input.value.trim().toLocaleLowerCase();
            const exact = options.some(option => option.dataset.value.toLocaleLowerCase() === typed);
            options.forEach(option => {
                const name = option.dataset.value.toLocaleLowerCase();
                option.hidden = !(showAll || exact || !typed || name.startsWith(typed) || name.includes(typed));
            });
            const any = visible().length > 0;
            list.hidden = !any;
            input.setAttribute('aria-expanded', String(any));
            setActive(-1);
        }

        function close() {
            list.hidden = true;
            input.setAttribute('aria-expanded', 'false');
            setActive(-1);
        }

        function choose(option) {
            input.value = option.dataset.value;
            close();
            input.dispatchEvent(new Event('change', {bubbles: true}));
        }

        input.addEventListener('focus', () => open(false));
        input.addEventListener('click', () => open(false));
        input.addEventListener('input', () => open(false));
        input.addEventListener('blur', () => setTimeout(close, 150));
        input.addEventListener('keydown', event => {
            if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
                event.preventDefault();
                if (list.hidden) open(false);
                setActive(active + (event.key === 'ArrowDown' ? 1 : -1));
            } else if (event.key === 'Enter' && !list.hidden && active >= 0) {
                event.preventDefault();
                choose(visible()[active]);
            } else if (event.key === 'Escape' && !list.hidden) {
                event.stopPropagation();
                close();
            }
        });
        toggle.addEventListener('mousedown', event => event.preventDefault());
        toggle.addEventListener('click', () => {
            if (list.hidden) {
                input.focus();
                open(true);
            } else {
                close();
            }
        });
        // mousedown keeps the focus in the field, so blur doesn't close the list first.
        options.forEach(option => {
            option.addEventListener('mousedown', event => event.preventDefault());
            option.addEventListener('click', () => choose(option));
        });
    }

    document.querySelectorAll('[data-meal-combo]').forEach(setup);
})();
