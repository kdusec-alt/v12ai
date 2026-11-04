import threading
import unittest
from unittest.mock import patch
from runtime_guard import analysis_slot


class RuntimeStabilityTests(unittest.TestCase):
    def test_analysis_lock_is_reentrant_and_released_after_error(self):
        with self.assertRaises(ValueError):
            with analysis_slot(0):
                with analysis_slot(0):raise ValueError('test')
        with analysis_slot(0):pass

    def test_busy_analysis_is_bounded_instead_of_allocating_another_graph(self):
        held=threading.Event();release=threading.Event()
        def worker():
            with analysis_slot(0):held.set();release.wait(2)
        thread=threading.Thread(target=worker);thread.start()
        try:
            self.assertTrue(held.wait(1))
            with self.assertRaisesRegex(RuntimeError,'稍後重試'):
                with analysis_slot(0.01):self.fail('concurrent allocation allowed')
        finally:release.set();thread.join(2)
        self.assertFalse(thread.is_alive())

    def test_streamlit_startup_and_repeated_rerenders(self):
        from streamlit.testing.v1 import AppTest
        import tino_persistent_store as store
        with patch.object(store,'ensure_memory_initialized_bootsafe',return_value={'remote_restore':{'status':'PASS'}}):
            app=AppTest.from_file('app.py',default_timeout=30)
            for _ in range(3):
                app.run()
                self.assertEqual(len(app.exception),0)


if __name__=='__main__':unittest.main()
