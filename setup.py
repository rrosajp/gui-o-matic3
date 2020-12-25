#!/usr/bin/python3
#
# SPDX-FileCopyrightText: © 2016-2018 Mailpile ehf. <team@mailpile.is>
# SPDX-FileCopyrightText: © 2016-2018 Bjarni Rúnar Einarsson <bre@godthaab.is>
# SPDX-FileCopyrightText: 🄯 2020 Peter J. Mello <admin@petermello.net>
#
# SPDX-License-Identifier: LGPL-3.0-only

import pathlib

try:
    import setuptools
except ImportError:
    from distribute_setup import use_setuptools
    use_setuptools()

# Do not edit: The VERSION gets updated by the update-version script
VERSION = '0.3.89'

here = pathlib.Path(__file__).parent.resolve()

long_description = (here / 'README.md')read_text(encoding='utf-8')

setuptools.setup(
    name='gui-o-matic',
    version=VERSION,
    author='Mailpile ehf.',
    author_email='team@mailpile.is',
    description='A cross-platform tool for minimal GUIs',
    long_description=long_description,
    long_description_content_type='text/markdown',
    url='https://github.com/mailpile/gui-o-matic/',
    license='LGPLv3',
    packages=setuptools.find_packages(where='gui_o_matic),
    keywords='notification, notify, mailpile',
    project_urls={
    'Repository': 'https://github.com/mailpile/gui-o-matic/',
    'Bug Tracker': 'https://github.com/mailpile/gui-o-matic/issues'
    },
    entry_points={
        'console_scripts': [
            'gui-o-matic = gui_o_matic.__main__:main'
        ]
    },
    classifiers=[
        'Development Status :: 4 - Beta',
        'Environment :: MacOS X',
        'Environment :: X11 Applications',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: GNU Lesser General Public License v3 (LGPLv3)',
        'Operating System :: MacOS :: MacOS X',
        'Operating System :: POSIX',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3 :: Only',
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: 3.7',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.9',
        'Programming Language :: Python :: 3.10',
        'Programming Language :: Python :: Implementation :: CPython',
        'Programming Language :: Python :: Implementation :: PyPy',
        'Topic :: Desktop Environment',
        'Topic :: Software Development :: Libraries :: Python Modules',
        'Topic :: Software Development :: User Interfaces',
    ],
    python_requires='~=3.6',
    install_requires=['gobject', 'python-dbus'],
    package_dir={'': 'gui_o_matic'}
)
