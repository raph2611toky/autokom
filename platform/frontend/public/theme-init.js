// Chargement initial du thème, exécuté depuis un fichier autorisé par la CSP.
try { var t = localStorage.getItem('autokom-theme') || localStorage.getItem('autokom-landing-theme'); document.documentElement.dataset.theme = (t === 'light' || t === 'dark') ? t : (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light') } catch (e) { document.documentElement.dataset.theme = 'light' }
