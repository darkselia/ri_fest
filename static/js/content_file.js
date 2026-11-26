document.addEventListener("DOMContentLoaded", () => {
    const file = document.querySelector('#contentfile_form input[type="file"][name="file"]');
    const name = document.querySelector('#contentfile_form input[name="name"]');
    if (!file || !name) return;

    file.addEventListener("change", () => {
        if (!name.value.trim() && file.files.length) {
            name.value = file.files[0].name;
        }
    });
});
