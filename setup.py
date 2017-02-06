from setuptools import setup
import os

_dir_ = os.path.dirname(__file__)


def long_description():
    """Returns the value of README.rst"""
    with open(os.path.join(_dir_, 'README.rst')) as f:
        return f.read()


setup(name='django-multisite',
      version='1.4.0',
      description='Serve multiple sites from a single Django application',
      long_description=long_description(),
      author='Leonid S Shestera',
      author_email='leonid@shestera.ru',
      maintainer='Ecometrica',
      maintainer_email='dev@ecometrica.com',
      url='http://github.com/ecometrica/django-multisite',
      packages=['multisite',
                'multisite.management',
                'multisite.management.commands',
                'multisite.migrations',
                'multisite.template',
                'multisite.template.loaders'],
      install_requires=['Django>=1.6',
                        'tldextract>=1.2'],
      test_suite="multisite.tests",
      classifiers=['Development Status :: 4 - Beta',
                   'Environment :: Web Environment',
                   'Framework :: Django',
                   'Intended Audience :: Developers',
                   'License :: OSI Approved :: BSD License',
                   'Operating System :: OS Independent',
                   'Programming Language :: Python',
                   'Programming Language :: Python :: 2.6',
                   'Programming Language :: Python :: 2.7',
                   'Programming Language :: Python :: 3.3',
                   'Topic :: Internet',
                   'Topic :: Internet :: WWW/HTTP',
                   'Topic :: Software Development :: Libraries',
                   'Topic :: Utilities'],
)
