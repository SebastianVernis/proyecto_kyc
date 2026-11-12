/* plan-gate.js — bloquea las páginas de herramienta a usuarios sin plan.
 *
 * El backend ya responde 403 NO_SUBSCRIPTION en toda la API de herramientas;
 * este script evita que la página cargue y muestre errores a medias, y manda
 * al usuario a /subscribe. También atrapa cualquier 403 de la API por si la
 * sesión pierde el plan mientras la página está abierta.
 */
(function () {
  var HERRAMIENTAS = {
    '/buscar': 1,
    '/buscar_direccion': 1,
    '/busquedas_adicionales': 1,
    '/sujeto': 1,
    '/oraculo': 1,
    '/cfe_coordenadas': 1,
    '/issste': 1,
    '/m': 1
  };
  var ruta = location.pathname.replace(/\.html$/, '').replace(/\/$/, '') || '/';
  if (!HERRAMIENTAS[ruta]) return;

  var original = window.fetch ? window.fetch.bind(window) : null;

  // Cualquier herramienta que conteste 403 NO_SUBSCRIPTION manda a planes.
  if (original) {
    window.fetch = function () {
      return original.apply(window, arguments).then(function (resp) {
        if (resp.status === 403) {
          resp.clone().json().then(function (data) {
            if (data && data.code === 'NO_SUBSCRIPTION') {
              location.replace('/subscribe.html');
            }
          }).catch(function () {});
        }
        return resp;
      });
    };
  }

  var token = localStorage.getItem('session_token');
  if (!token) {
    location.replace('/login.html');
    return;
  }
  if (!original) return;

  var headers = {'Authorization': 'Bearer ' + token};
  Promise.all([
    original('/api/auth/me', {headers: headers}),
    original('/api/subscribe/status', {headers: headers})
  ]).then(function (rs) {
    if (!rs[0].ok || !rs[1].ok) return null;
    return Promise.all([rs[0].json(), rs[1].json()]);
  }).then(function (d) {
    if (!d) return;
    if (d[0].user && d[0].user.is_admin) return;
    if (!d[1].has_subscription) location.replace('/subscribe.html');
  }).catch(function () {});
})();
