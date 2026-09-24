// Argos OSINT — Landing Page JS
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('a[href^="#"]').forEach(a => {
    a.addEventListener('click', (e) => {
      e.preventDefault();
      const target = document.querySelector(a.getAttribute('href'));
      if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  });
  const navbar = document.querySelector('.navbar');
  if (navbar) {
    window.addEventListener('scroll', () => {
      navbar.style.background = window.scrollY > 50 ? 'rgba(17,24,39,0.95)' : 'var(--bg-secondary)';
      navbar.style.backdropFilter = window.scrollY > 50 ? 'blur(10px)' : 'none';
    });
  }
});
