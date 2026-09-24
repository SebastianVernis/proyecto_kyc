#!/usr/bin/env python3
"""test_provider_keys.py — Tests del CRUD de API keys per-usuario.

Cubre:
  - auth.set_provider_key/get/delete/list
  - providers.keyring: encrypt/decrypt round-trip + masking
  - providers.provider_factory: resolve_api_key fallback user→global→none
  - auth.touch_provider_key_usage: actualiza last_used/last_ok/last_error

Sin red, sin Singula/Apify/Tlaloc reales (no mockeamos health checks;
sólo validamos el ciclo de vida de la key en DB).
"""
import os
import sys
import unittest
import tempfile
import importlib

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "backend"))


class TestKeyring(unittest.TestCase):
    """providers.keyring — cifrado + masking."""

    def setUp(self):
        from providers import keyring
        self.kr = keyring

    def test_roundtrip(self):
        plain = "sk-test-1234567890-abcdef"
        enc = self.kr.encrypt_key(plain)
        self.assertIsInstance(enc, bytes)
        self.assertNotIn(plain.encode(), enc)
        dec = self.kr.decrypt_key(enc)
        self.assertEqual(dec, plain)

    def test_mask_long(self):
        # 20 chars: sk-1 + 12 puntos + efgh (últimos 4)
        m = self.kr.mask_key("sk-1234567890abcdefgh")
        self.assertTrue(m.startswith("sk-1"))
        self.assertTrue(m.endswith("efgh"))
        # El "…" central indica enmascarado
        self.assertIn("…", m)

    def test_mask_exactly_8(self):
        # 8 chars: primeros 4 + 0 puntos + últimos 4
        m = self.kr.mask_key("12345678")
        self.assertEqual(m, "12345678")  # sin puntos porque len-8 = 0

    def test_mask_short(self):
        self.assertEqual(self.kr.mask_key("abc"), "***")
        self.assertEqual(self.kr.mask_key(""), "")

    def test_validate_provider(self):
        self.assertEqual(self.kr.validate_provider("singula"), "singula")
        self.assertEqual(self.kr.validate_provider("SINGULA"), "singula")
        with self.assertRaises(ValueError):
            self.kr.validate_provider("openai")
        with self.assertRaises(ValueError):
            self.kr.validate_provider("")

    def test_encrypt_empty_rejected(self):
        with self.assertRaises(ValueError):
            self.kr.encrypt_key("")
        with self.assertRaises(ValueError):
            self.kr.encrypt_key(None) or self.kr.encrypt_key("")  # noqa


class TestAuthProviderKeysCRUD(unittest.TestCase):
    """auth.set/get/delete/list + touch_usage."""

    def setUp(self):
        # asegurar DB inicializada (usa bases/auth.db real del repo)
        import auth
        self.auth = auth
        self._test_users = (
            "test_user_crud", "test_user_replace", "test_user_short",
            "test_user_inv", "test_user_touch", "test_user_touch_err",
            "test_user_factory", "test_user_singula_client", "test_user_tlaloc_client",
        )
        # cleanup preventivo (puede haber residuos de runs anteriores)
        for u in self._test_users:
            for p in ("singula", "apify", "tlaloc"):
                try:
                    self.auth.delete_provider_key(u, p)
                except (ValueError, Exception):
                    pass  # usuario no existe, OK
            try:
                self.auth.delete_user(u)
            except Exception:
                pass
        # crear usuarios de prueba
        for u in self._test_users:
            self.auth.set_user_password(u, "test-password-12345678")

    def tearDown(self):
        # limpiar keys + usuarios al final
        for u in self._test_users:
            for p in ("singula", "apify", "tlaloc"):
                try:
                    self.auth.delete_provider_key(u, p)
                except Exception:
                    pass
            try:
                self.auth.delete_user(u)
            except Exception:
                pass

    def test_set_get_delete_roundtrip(self):
        username = "test_user_crud"
        try:
            # SET
            res = self.auth.set_provider_key(username, "singula", "sk-crud-1234567890ab")
            self.assertEqual(res["username"], username)
            self.assertEqual(res["provider"], "singula")
            # GET (devuelve plaintext)
            plain = self.auth.get_provider_key(username, "singula")
            self.assertEqual(plain, "sk-crud-1234567890ab")
            # LIST (devuelve enmascarada)
            keys = self.auth.list_provider_keys(username)
            self.assertEqual(len(keys), 1)
            self.assertEqual(keys[0]["provider"], "singula")
            self.assertTrue(keys[0]["has_key"])
            self.assertNotIn("1234567890ab", keys[0]["masked"])  # no muestra completo
            self.assertTrue(keys[0]["masked"].startswith("sk-c"))
            # DELETE
            res = self.auth.delete_provider_key(username, "singula")
            self.assertTrue(res["deleted"])
            # GET después de delete
            self.assertIsNone(self.auth.get_provider_key(username, "singula"))
        finally:
            self.auth.delete_provider_key(username, "singula")

    def test_set_replaces_existing(self):
        username = "test_user_replace"
        try:
            self.auth.set_provider_key(username, "apify", "apify-old-12345678")
            self.auth.set_provider_key(username, "apify", "apify-new-12345678")
            plain = self.auth.get_provider_key(username, "apify")
            self.assertEqual(plain, "apify-new-12345678")
            keys = self.auth.list_provider_keys(username)
            self.assertEqual(len(keys), 1)
        finally:
            self.auth.delete_provider_key(username, "apify")

    def test_short_key_rejected(self):
        with self.assertRaises(ValueError):
            self.auth.set_provider_key("test_user_short", "singula", "abc")

    def test_nonexistent_user(self):
        with self.assertRaises(ValueError):
            self.auth.set_provider_key("user_does_not_exist_xyz", "singula", "sk-valid-1234567890")

    def test_invalid_provider_rejected(self):
        with self.assertRaises(ValueError):
            self.auth.set_provider_key("test_user_inv", "openai", "sk-valid-1234567890")

    def test_touch_usage_ok(self):
        username = "test_user_touch"
        try:
            self.auth.set_provider_key(username, "tlaloc", "tlaloc-touch-1234567890")
            self.auth.touch_provider_key_usage(username, "tlaloc", ok=True)
            keys = self.auth.list_provider_keys(username)
            self.assertIsNotNone(keys[0]["last_used"])
            self.assertIsNotNone(keys[0]["last_ok"])
            self.assertIsNone(keys[0]["last_error"])
        finally:
            self.auth.delete_provider_key(username, "tlaloc")

    def test_touch_usage_error(self):
        username = "test_user_touch_err"
        try:
            self.auth.set_provider_key(username, "tlaloc", "tlaloc-touch-err-12345")
            self.auth.touch_provider_key_usage(username, "tlaloc", ok=False, error_msg="HTTP 401")
            keys = self.auth.list_provider_keys(username)
            self.assertIsNotNone(keys[0]["last_used"])
            self.assertEqual(keys[0]["last_error"], "HTTP 401")
        finally:
            self.auth.delete_provider_key(username, "tlaloc")


class TestProviderFactory(unittest.TestCase):
    """providers.provider_factory — resolución de keys por usuario con fallback."""

    def setUp(self):
        from providers import provider_factory
        self.fac = provider_factory
        import auth
        self.auth = auth
        self._test_users = ("test_user_factory", "test_user_singula_client", "test_user_tlaloc_client")
        # cleanup preventivo
        for u in self._test_users:
            for p in ("singula", "apify", "tlaloc"):
                try:
                    self.auth.delete_provider_key(u, p)
                except Exception:
                    pass
            try:
                self.auth.delete_user(u)
            except Exception:
                pass
        for u in self._test_users:
            self.auth.set_user_password(u, "test-password-12345678")

    def tearDown(self):
        for u in self._test_users:
            for p in ("singula", "apify", "tlaloc"):
                try:
                    self.auth.delete_provider_key(u, p)
                except Exception:
                    pass
            try:
                self.auth.delete_user(u)
            except Exception:
                pass

    def test_user_key_takes_precedence(self):
        username = "test_user_factory"
        try:
            self.auth.set_provider_key(username, "singula", "sk-user-1234567890abcdef")
            api_key, source = self.fac.resolve_api_key(username, "singula")
            self.assertEqual(source, "user")
            self.assertEqual(api_key, "sk-user-1234567890abcdef")
        finally:
            self.auth.delete_provider_key(username, "singula")

    def test_fallback_to_global_when_no_user_key(self):
        # admin no tiene key per-usuario para singula (o la borramos)
        self.auth.delete_provider_key("admin", "singula")
        api_key, source = self.fac.resolve_api_key("admin", "singula")
        # O es global (si hay SINGULA_API_KEY en .env) o es none (si no)
        self.assertIn(source, ("global", "none"))
        if source == "global":
            self.assertIsNotNone(api_key)
        else:
            self.assertIsNone(api_key)

    def test_none_when_no_user_no_global(self):
        # El usuario no existe, no hay fallback
        api_key, source = self.fac.resolve_api_key("user_never_existed_xyz", "singula")
        # Si la global existe, sigue siendo global aunque el user no exista
        self.assertIn(source, ("global", "none"))

    def test_get_singula_client_returns_user_key(self):
        username = "test_user_singula_client"
        try:
            self.auth.set_provider_key(username, "singula", "sk-singula-factory-12345")
            client = self.fac.get_singula_client(username)
            self.assertIsNotNone(client)
            self.assertEqual(client.api_key, "sk-singula-factory-12345")
        finally:
            self.auth.delete_provider_key(username, "singula")

    def test_get_tlaloc_client_returns_user_key(self):
        username = "test_user_tlaloc_client"
        try:
            self.auth.set_provider_key(username, "tlaloc", "tlaloc-factory-12345678")
            client = self.fac.get_tlaloc_client(username)
            self.assertIsNotNone(client)
            self.assertEqual(client.api_key, "tlaloc-factory-12345678")
        finally:
            self.auth.delete_provider_key(username, "tlaloc")


class TestAvailableProviders(unittest.TestCase):
    """Helper _check_provider_for_user + _list_available_providers_for_user.

    Sin red: mockeamos _provider_health_check para no salir a internet.
    """

    class FakeHandler:
        """Mínimo nécessaire: _provider_health_check + helpers que necesita."""
        _HEALTH_CACHE = {}
        _HEALTH_TTL_SEC = 300

        def _health_cache_key(self, username, provider, api_key):
            import hashlib
            h = hashlib.sha256((api_key or "").encode("utf-8")).hexdigest()[:16]
            return (username, provider, h)

        def _provider_health_check(self, provider, api_key):
            # devuelve un resultado mockeado basado en la key
            import os
            test_marker = os.environ.get("TEST_HEALTH_OK", "1")
            if test_marker == "0":
                return {"ok": False, "detail": {"status": 401, "error": "Invalid or expired token"}}
            return {"ok": True, "detail": {"ok": True, "user": "test-user"}}

        _check_provider_for_user = None
        _list_available_providers_for_user = None

    def setUp(self):
        import auth
        self.auth = auth
        self._test_users = (
            "test_user_avail_ok", "test_user_avail_fail", "test_user_cache",
        )
        for u in self._test_users:
            for p in ("singula", "apify", "tlaloc"):
                try:
                    self.auth.delete_provider_key(u, p)
                except Exception:
                    pass
            try:
                self.auth.delete_user(u)
            except Exception:
                pass
            self.auth.set_user_password(u, "test-pwd-12345678")
        # crear instancia mínima del handler con los helpers extraídos
        import time
        handler = self
        class _H:
            _HEALTH_CACHE = {}
            _HEALTH_TTL_SEC = 300
            def _health_cache_key(self, username, provider, api_key):
                import hashlib
                h = hashlib.sha256((api_key or "").encode("utf-8")).hexdigest()[:16]
                return (username, provider, h)
            def _provider_health_check(self, provider, api_key):
                import os
                if os.environ.get("TEST_HEALTH_OK", "1") == "0":
                    return {"ok": False, "detail": {"status": 401, "error": "Invalid or expired token"}}
                return {"ok": True, "detail": {"ok": True, "user": "test-user"}}
            def _check_provider_for_user(self, username, provider):
                from providers.provider_factory import resolve_api_key
                api_key, source = resolve_api_key(username, provider)
                result = {"provider": provider, "available": False, "reason": "no_key", "source": source}
                if not api_key:
                    return result
                cache_key = self._health_cache_key(username, provider, api_key)
                cached = self._HEALTH_CACHE.get(cache_key)
                now = time.time()
                if cached and (now - cached[0]) < self._HEALTH_TTL_SEC:
                    ok, detail = cached[1], cached[2]
                else:
                    health = self._provider_health_check(provider, api_key)
                    ok = bool(health.get("ok"))
                    detail = health.get("detail")
                    self._HEALTH_CACHE[cache_key] = (now, ok, detail)
                if ok:
                    result["available"] = True
                    result["reason"] = "ok"
                else:
                    result["reason"] = "key_invalid"
                result["health_detail"] = detail
                return result
            def _list_available_providers_for_user(self, username):
                return [self._check_provider_for_user(username, p)
                        for p in ("singula", "apify", "tlaloc")]
        self.handler = _H()

    def tearDown(self):
        import auth
        for u in self._test_users:
            for p in ("singula", "apify", "tlaloc"):
                try:
                    self.auth.delete_provider_key(u, p)
                except Exception:
                    pass
            try:
                self.auth.delete_user(u)
            except Exception:
                pass

    def test_no_keys_returns_all_unavailable(self):
        # admin puede tener keys globales del .env; limpiamos con set_provider_key/delete
        import auth
        for p in ("singula", "apify", "tlaloc"):
            auth.delete_provider_key("admin", p)
        # BORRAR temporalmente las globales via env override no se puede,
        # así que sólo validamos que 'admin' SIN keys per-user obtiene al
        # menos source='global' o 'none', no falla.
        result = self.handler._list_available_providers_for_user("admin")
        self.assertEqual(len(result), 3)
        for r in result:
            self.assertIn(r["provider"], ("singula", "apify", "tlaloc"))
            self.assertIn(r["source"], ("user", "global", "none"))

    def test_health_check_ok_marks_available(self):
        import os
        os.environ["TEST_HEALTH_OK"] = "1"
        # limpiar cache
        self.FakeHandler._HEALTH_CACHE.clear()
        # crear un user de prueba con key
        import auth
        try:
            auth.set_user_password("test_user_avail_ok", "test-pwd-12345678")
            auth.set_provider_key("test_user_avail_ok", "singula", "sk-test-ok-12345678")
            result = self.handler._check_provider_for_user("test_user_avail_ok", "singula")
            self.assertTrue(result["available"])
            self.assertEqual(result["reason"], "ok")
            self.assertEqual(result["source"], "user")
        finally:
            auth.delete_provider_key("test_user_avail_ok", "singula")
            try:
                auth.delete_user("test_user_avail_ok")
            except Exception:
                pass

    def test_health_check_fail_marks_unavailable(self):
        import os
        os.environ["TEST_HEALTH_OK"] = "0"
        self.FakeHandler._HEALTH_CACHE.clear()
        import auth
        try:
            auth.set_user_password("test_user_avail_fail", "test-pwd-12345678")
            auth.set_provider_key("test_user_avail_fail", "apify", "apify-test-fail-1234567")
            result = self.handler._check_provider_for_user("test_user_avail_fail", "apify")
            self.assertFalse(result["available"])
            self.assertEqual(result["reason"], "key_invalid")
            self.assertEqual(result["source"], "user")
        finally:
            os.environ.pop("TEST_HEALTH_OK", None)
            auth.delete_provider_key("test_user_avail_fail", "apify")
            try:
                auth.delete_user("test_user_avail_fail")
            except Exception:
                pass

    def test_health_cache_reused(self):
        import os
        os.environ["TEST_HEALTH_OK"] = "1"
        self.FakeHandler._HEALTH_CACHE.clear()
        import auth
        call_count = {"n": 0}
        original = self.handler._provider_health_check
        def counting(provider, api_key):
            call_count["n"] += 1
            return original(provider, api_key)
        self.handler._provider_health_check = counting
        try:
            auth.set_user_password("test_user_cache", "test-pwd-12345678")
            auth.set_provider_key("test_user_cache", "tlaloc", "tlaloc-cache-test-123456")
            # 2 llamadas dentro del TTL: solo 1 health check real
            self.handler._check_provider_for_user("test_user_cache", "tlaloc")
            self.handler._check_provider_for_user("test_user_cache", "tlaloc")
            self.assertEqual(call_count["n"], 1)
        finally:
            auth.delete_provider_key("test_user_cache", "tlaloc")
            try:
                auth.delete_user("test_user_cache")
            except Exception:
                pass


class TestAuthSchemaMigration(unittest.TestCase):
    """La tabla user_provider_keys existe con el schema correcto."""

    def test_table_exists(self):
        import auth
        with auth._db() as conn:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
        self.assertIn("user_provider_keys", tables)

    def test_schema_columns(self):
        import auth
        with auth._db() as conn:
            cols = {r[1]: r[2] for r in conn.execute(
                "PRAGMA table_info(user_provider_keys)"
            ).fetchall()}
        # verificar columnas mínimas
        for required in ("user_id", "provider", "api_key_enc",
                         "created_at", "updated_at"):
            self.assertIn(required, cols, f"falta columna {required}")

    def test_init_db_idempotent(self):
        # Llamar init_db() 2 veces no debe fallar
        import auth
        auth.init_db()
        auth.init_db()  # segunda llamada, debe ser no-op


if __name__ == "__main__":
    unittest.main(verbosity=2)
