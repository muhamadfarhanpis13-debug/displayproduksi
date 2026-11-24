// Interaksi kecil yang dipakai di banyak halaman.
(function () {
  // Buka <dialog> lewat tombol [data-dialog="id"]
  document.addEventListener("click", function (e) {
    var opener = e.target.closest("[data-dialog]");
    if (opener) {
      e.preventDefault();
      var dlg = document.getElementById(opener.getAttribute("data-dialog"));
      if (dlg && dlg.showModal) dlg.showModal();
      return;
    }
    if (e.target.closest("[data-close]")) {
      var d = e.target.closest("dialog");
      if (d) d.close();
      return;
    }
    // klik di luar kotak dialog menutupnya
    if (e.target.tagName === "DIALOG") e.target.close();
  });

  // Select yang langsung mengirim form saat diubah
  document.querySelectorAll("select[data-autosubmit]").forEach(function (sel) {
    sel.addEventListener("change", function () { sel.form.submit(); });
  });

  // Konfirmasi sebelum aksi berbahaya
  document.querySelectorAll("form[data-confirm]").forEach(function (form) {
    form.addEventListener("submit", function (e) {
      if (!window.confirm(form.getAttribute("data-confirm"))) e.preventDefault();
    });
  });

  // Dropzone unggah file
  document.querySelectorAll("[data-dropzone]").forEach(function (zone) {
    var input = zone.querySelector("input[type=file]");
    var list = zone.querySelector("[data-dropzone-files]");
    function render() {
      var names = Array.prototype.map.call(input.files, function (f) { return f.name; });
      list.textContent = names.length ? names.length + " file: " + names.join(", ") : "";
    }
    input.addEventListener("change", render);
    ["dragenter", "dragover"].forEach(function (ev) {
      zone.addEventListener(ev, function (e) { e.preventDefault(); zone.classList.add("is-over"); });
    });
    ["dragleave", "drop"].forEach(function (ev) {
      zone.addEventListener(ev, function (e) { e.preventDefault(); zone.classList.remove("is-over"); });
    });
    zone.addEventListener("drop", function (e) {
      if (e.dataTransfer && e.dataTransfer.files.length) { input.files = e.dataTransfer.files; render(); }
    });
  });

  // Tombol submit jadi "Memproses…" supaya tidak terkirim dua kali (khusus unggah)
  document.querySelectorAll("form[enctype='multipart/form-data']").forEach(function (form) {
    form.addEventListener("submit", function () {
      var btn = form.querySelector("button[type=submit]");
      if (btn) { btn.disabled = true; btn.textContent = "Mengunggah…"; }
    });
  });
})();
