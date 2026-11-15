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
  'rubberband'     # LADSPA pitch shifter used by pitch presets
  'python-gobject'
  'gtk4'
  'libadwaita'
)
makedepends=('python-build' 'python-installer' 'python-setuptools' 'python-wheel')
source=()
sha256sums=()

build() {
  cd "$startdir"
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
