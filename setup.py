import os
import sys

from setuptools import find_packages, setup

_dir_ = os.path.dirname(__file__)


if sys.version_info < (3, 4):
    install_requires = ['Django>=1.8,<2.0', 'tldextract>=1.2']
else:
    install_requires = ['Django>=1.8,<2.3', 'tldextract>=1.2']


def long_description():
    """Returns the value of README.rst"""
    with open(os.path.join(_dir_, 'README.rst')) as f:
        return f.read()

here = os.path.abspath(_dir_)
version = {}
with open(os.path.join(here, 'multisite', '__version__.py')) as f:
    exec(f.read(), version)


files = ["multisite/test_templates/*"]

setup(name='django-multisite',
      version=version['__version__'],
      description='Serve multiple sites from a single Django application',
      long_description=long_description(),
      author='Leonid S Shestera',
      author_email='leonid@shestera.ru',
      maintainer='Ecometrica',
      maintainer_email='dev@ecometrica.com',
      url='http://github.com/ecometrica/django-multisite',
      packages=find_packages(),
      include_package_data=True,
      package_data={'multisite': files},
      install_requires=install_requires,
      setup_requires=['pytest-runner'],
      tests_require=['coverage', 'mock', 'pytest', 'pytest-cov',
                     'pytest-django', 'pytest-pythonpath', 'tox'],
      test_suite="multisite.tests",
      classifiers=['Development Status :: 4 - Beta',
                   'Environment :: Web Environment',
                   'Framework :: Django',
                   'Intended Audience :: Developers',
                   'License :: OSI Approved :: BSD License',
                   'Operating System :: OS Independent',
                   'Programming Language :: Python',
                   'Programming Language :: Python :: 2.7',
                   'Programming Language :: Python :: 3.4',
                   'Programming Language :: Python :: 3.5',
                   'Programming Language :: Python :: 3.6',
                   'Topic :: Internet',
                   'Topic :: Internet :: WWW/HTTP',
                   'Topic :: Software Development :: Libraries',
                   'Topic :: Utilities'],
      )
