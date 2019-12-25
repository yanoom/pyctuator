# pylint: disable=import-outside-toplevel
import importlib.util
from datetime import datetime, timezone
from typing import Any, Optional, Dict, Callable

# A note about imports: this module ensure that only relevant modules are imported.
# For example, if the webapp is a Flask webapp, we do not want to import FastAPI, and vice versa.
# To do that, all imports are in conditional branches after detecting which frameworks are installed.
# DO NOT add any web-framework-dependent imports to the global scope.
from pyctuator.environment.custom_environment_provider import CustomEnvironmentProvider
from pyctuator.environment.os_env_variables_impl import OsEnvironmentVariableProvider
from pyctuator.health.diskspace_health_impl import DiskSpaceHealthProvider
from pyctuator.metrics.memory_metrics_impl import MemoryMetricsProvider
from pyctuator.metrics.thread_metrics_impl import ThreadMetricsProvider
from pyctuator.pyctuator_impl import PyctuatorImpl, AppInfo, BuildInfo, GitInfo, GitCommitInfo, AppDetails
from pyctuator.spring_boot_admin_registration import BootAdminRegistrationHandler


class Pyctuator:

    def __init__(
            self,
            app: Any,
            app_name: str,
            app_url: str,
            pyctuator_endpoint_url: str,
            registration_url: Optional[str],
            app_description: Optional[str] = None,
            registration_interval_sec: int = 10,
            free_disk_space_down_threshold_bytes: int = 1024 * 1024 * 100,
    ) -> None:
        """The entry point for integrating pyctuator with a web-frameworks such as FastAPI and Flask.

        Given an application built on top of a supported web-framework, it'll add to the application the REST API
         endpoints that required for Spring Boot Admin to monitor and manage the application.

        Pyctuator currently supports application built on top of FastAPI and Flask. The type of first argument, app is
         specific to the target web-framework:

        * FastAPI - `app` is an instance of `fastapi.applications.FastAPI`

        * Flask - `app` is an instance of `flask.app.Flask`

        :param app: an instance of a supported web-framework with which the pyctuator endpoints will be registered
        :param app_name: the application's name that will be presented in the "Info" section in boot-admin
        :param app_description: a description that will be presented in the "Info" section in boot-admin
        :param app_url: the full URL of the application being monitored which will be displayed in spring-boot-admin, we
         recommend this URL to be accessible by those who manage the application (i.e. don't use "http://localhost..."
         as it is only accessible from within the application's host)
        :param pyctuator_endpoint_url: the public URL from which Pyctuator REST API will be accessible, used for
        registering the application with spring-boot-admin, must be accessible from spring-boot-admin server (i.e. don't
        use http://localhost:8080/... unless spring-boot-admin is running on the same host as the monitored application)
        :param registration_url: the spring-boot-admin endpoint to which registration requests must be posted
        :param registration_interval_sec: how often pyctuator will renew its registration with spring-boot-admin
        :param free_disk_space_down_threshold_bytes: amount of free space in bytes in "./" (the application's current
         working directory) below which the built-in disk-space health-indicator will fail
        """

        start_time = datetime.now(timezone.utc)

        # Instantiate an instance of PyctuatorImpl which abstracts the state and logic of the pyctuator
        self.pyctuator_impl = PyctuatorImpl(
            AppInfo(app=AppDetails(name=app_name, description=app_description)),
            pyctuator_endpoint_url,
        )

        # Register default health/metrics/environment providers
        self.pyctuator_impl.register_environment_provider(OsEnvironmentVariableProvider())
        self.pyctuator_impl.register_health_providers(DiskSpaceHealthProvider(free_disk_space_down_threshold_bytes))
        self.pyctuator_impl.register_metrics_provider(MemoryMetricsProvider())
        self.pyctuator_impl.register_metrics_provider(ThreadMetricsProvider())

        self.boot_admin_registration_handler: Optional[BootAdminRegistrationHandler] = None

        # Find and initialize an integration layer between the web-framework adn pyctuator
        framework_integrations = {
            "flask": self._integrate_flask,
            "fastapi": self._integrate_fastapi,
        }
        for framework_name, framework_integration_function in framework_integrations.items():
            if self._is_framework_installed(framework_name):
                success = framework_integration_function(app, self.pyctuator_impl)
                if success:
                    if registration_url is not None:
                        self.boot_admin_registration_handler = BootAdminRegistrationHandler(
                            registration_url,
                            app_name,
                            self.pyctuator_impl.pyctuator_endpoint_url,
                            start_time,
                            app_url,
                            registration_interval_sec,
                        )
                        self.boot_admin_registration_handler.start()
                    return

        # Fail in case no framework was found for the target app
        raise EnvironmentError("No framework was found that is matching the target app"
                               "(is it properly installed and imported?)")

    def stop(self) -> None:
        if self.boot_admin_registration_handler:
            self.boot_admin_registration_handler.stop()
        self.boot_admin_registration_handler = None

    def register_environment_provider(self, name: str, env_provider: Callable[[], Dict]) -> None:
        self.pyctuator_impl.environment_providers.append(CustomEnvironmentProvider(name, env_provider))

    def set_git_info(self, commit: str, time: datetime, branch: Optional[str] = None) -> None:
        self.pyctuator_impl.set_git_info(GitInfo(GitCommitInfo(time, commit), branch))

    def set_build_info(
            self,
            artifact: Optional[str] = None,
            group: Optional[str] = None,
            name: Optional[str] = None,
            version: Optional[str] = None,
            time: Optional[datetime] = None,
    ) -> None:
        self.pyctuator_impl.set_build_info(BuildInfo(name, artifact, group, version, time))

    def _is_framework_installed(self, framework_name: str) -> bool:
        return importlib.util.find_spec(framework_name) is not None

    def _integrate_fastapi(self, app: Any, pyctuator_impl: PyctuatorImpl) -> bool:
        """
        This method should only be called if we detected that FastAPI is installed.
        It will then check whether the given app is a FastAPI app, and if so - it will add the Pyctuator
        endpoints to it.
        """
        from fastapi import FastAPI
        if isinstance(app, FastAPI):
            from pyctuator.fastapi_pyctuator import FastApiPyctuator
            FastApiPyctuator(app, pyctuator_impl)
            return True
        return False

    def _integrate_flask(self, app: Any, pyctuator_impl: PyctuatorImpl) -> bool:
        """
        This method should only be called if we detected that Flask is installed.
        It will then check whether the given app is a Flask app, and if so - it will add the Pyctuator
        endpoints to it.
        """
        from flask import Flask
        if isinstance(app, Flask):
            from pyctuator.flask_pyctuator import FlaskPyctuator
            FlaskPyctuator(app, pyctuator_impl)
            return True
        return False
