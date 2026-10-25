# Paquet .rpm de la base GTK4 gcm4 (#157), pendant de build-deb/ (#156).
# Construit par build-rpm/build.sh, qui fournit la version (celle de
# pyproject.toml, --define gcm4_version) et l'archive des sources : le wheel
# de pyproject.toml, data/, les catalogues compilés et le lanceur.
#
# Contenu identique au .deb : wheel décompressé dans /usr/share/gcm4 (dossier
# privé, avec le .dist-info où « gcm4 --version » lit la version), lanceur
# /usr/bin/gcm4, lanceur .desktop, metainfo, icônes, catalogues .mo.

Name:           gcm4
Version:        %{gcm4_version}
# Paquet noarch unique pour Fedora et EL : pas de %%{?dist}.
Release:        1
Summary:        Tabbed connection manager (GTK4 base, no protocol plugin)
License:        GPL-3.0-or-later
URL:            https://github.com/Mathilde-Team/Gcm4
BuildArch:      noarch
Source0:        gcm4-%{version}.tar.gz

# Base GTK4 sans aucun plugin (#126) : PyGObject, GTK 4 et libadwaita
# (D1 de #118), loguru. Aucune dépendance de plugin (VTE, gtk-vnc, spice,
# freerdp) : chaque plugin apporte les siennes dans son propre paquet.
# gobject-introspection : seul paquet qui fournit le typelib cairo-1.0,
# exigé par celui de GTK 4 ; ni gtk4 ni python3-gobject ne l'installent
# (import de Gtk 4.0 en échec sur Fedora 44 sans lui, #157).
Requires:       python3 >= 3.12
Requires:       python3-gobject
Requires:       gobject-introspection
Requires:       gtk4
Requires:       libadwaita
Requires:       python3-loguru
# Les modules de /usr/share/gcm4 sont privés : rien à exporter vers d'autres
# paquets (pas de « python3dist(...) » automatique).
%global __provides_exclude_from ^%{_datadir}/gcm4/.*$
%global __requires_exclude_from ^%{_datadir}/gcm4/.*$

%description
gcm4 organizes hosts in groups and opens their connections in tabs.

This package contains the GTK4 base only: host tree with groups and
search, host dialog, preferences, command line. It ships no connection
plugin, so no connection can be opened until a plugin package (SSH,
RDP, VNC...) is installed.

%prep
%setup -q

%build
# Python pur : le wheel est construit par build-rpm/build.sh.

%install
mkdir -p %{buildroot}%{_datadir}/gcm4
python3 -m zipfile -e gcm4-%{version}-py3-none-any.whl %{buildroot}%{_datadir}/gcm4
install -D -m 755 gcm4 %{buildroot}%{_bindir}/gcm4
install -D -m 644 data/io.github.MathildeDec.gcm4.desktop \
    %{buildroot}%{_datadir}/applications/io.github.MathildeDec.gcm4.desktop
install -D -m 644 data/io.github.MathildeDec.gcm4.metainfo.xml \
    %{buildroot}%{_datadir}/metainfo/io.github.MathildeDec.gcm4.metainfo.xml
(cd data/icons && find hicolor -name 'io.github.MathildeDec.gcm4.png' \
    -exec install -D -m 644 {} %{buildroot}%{_datadir}/icons/{} \;)
mkdir -p %{buildroot}%{_datadir}/locale
cp -r locale/. %{buildroot}%{_datadir}/locale/
# Octets compilés quand les macros Python de la distribution sont là
# (construction sur Fedora, job CI) ; sinon compilés en mémoire au démarrage.
%{?py_byte_compile:%py_byte_compile %{python3} %{buildroot}%{_datadir}/gcm4}
%find_lang gcm-lang

%files -f gcm-lang.lang
%license LICENSE
%{_bindir}/gcm4
%{_datadir}/gcm4/
%{_datadir}/applications/io.github.MathildeDec.gcm4.desktop
%{_datadir}/metainfo/io.github.MathildeDec.gcm4.metainfo.xml
%{_datadir}/icons/hicolor/*/apps/io.github.MathildeDec.gcm4.png

%changelog
* Wed Oct 21 2026 MathildeDec <mathilde.deuscher@gmail.com> - %{gcm4_version}-1
- Paquet de la base GTK4 gcm4, sans plugin.
