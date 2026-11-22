# Maintainer: newnetmp3
# Build from a checkout of this repository:  makepkg -si
pkgname=arch-voice
pkgver=0.1.0
pkgrel=1
pkgdesc="Real-time voice changer for PipeWire with a GTK 4 GUI and dozens of presets"
arch=('any')
url="https://github.com/newnetmp3/Arch-Voice"
license=('MIT')
depends=(
  'python'
  'pipewire'       # filter-chain, loopback, pw-cli, pw-link, pw-dump
  'wireplumber'    # session manager that connects the microphone
  'rubberband-ladspa'  # LADSPA pitch shifter used by pitch presets
  'python-gobject'
  'gtk4'
  'libadwaita'
)
makedepends=('git' 'python-build' 'python-installer' 'python-setuptools' 'python-wheel')
source=()
sha256sums=()

# The version includes the commit, so after a 'git pull' makepkg builds a new
# package instead of reinstalling the previously built one.
pkgver() {
  cd "$startdir"
  printf "0.1.0.r%s.g%s" "$(git rev-list --count HEAD)" "$(git rev-parse --short=7 HEAD)"
}

build() {
  cd "$startdir"
  rm -rf build ./*.egg-info  # never package stale copies of the modules
  python -m build --wheel --no-isolation --outdir "$srcdir/dist"
}

check() {
  cd "$startdir"
  XDG_CACHE_HOME="$srcdir/cache" python -m unittest discover -s tests
}

package() {
  python -m installer --destdir="$pkgdir" "$srcdir"/dist/*.whl
  install -Dm644 "$startdir/data/arch-voice.service" \
    "$pkgdir/usr/lib/systemd/user/arch-voice.service"
  install -Dm644 "$startdir/data/io.github.newnetmp3.ArchVoice.desktop" \
    "$pkgdir/usr/share/applications/io.github.newnetmp3.ArchVoice.desktop"
  install -Dm644 "$startdir/LICENSE" "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
}
