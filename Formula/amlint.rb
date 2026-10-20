class Amlint < Formula
  include Language::Python::Virtualenv

  desc "Semantic linter for Prometheus Alertmanager configs"
  homepage "https://github.com/danikdanik2013/amlint"
  url "https://files.pythonhosted.org/packages/27/01/ef48234b9d3c12fcda939cca4699e323281a573e5a3e8fd92ab3790805db/amlint-0.8.0.tar.gz"
  sha256 "5d8339e620518bc03062dc25749c21d178c44ac5cb4a53089e9385a2d4bd15f6"
  license "MIT"

  depends_on "libyaml"
  depends_on "python@3.12"

  # The release workflow updates url and sha256 above (scripts/bump_formula.py), never the
  # resources. They are made for these runtime dependencies in pyproject.toml; when those
  # change, the workflow stops until the resources and this line are updated by hand.
  # dependencies: pyyaml>=5.1, rich>=13

  resource "markdown-it-py" do
    url "https://files.pythonhosted.org/packages/06/ff/7841249c247aa650a76b9ee4bbaeae59370dc8bfd2f6c01f3630c35eb134/markdown_it_py-4.2.0.tar.gz"
    sha256 "04a21681d6fbb623de53f6f364d352309d4094dd4194040a10fd51833e418d49"
  end

  resource "mdurl" do
    url "https://files.pythonhosted.org/packages/d6/54/cfe61301667036ec958cb99bd3efefba235e65cdeb9c84d24a8293ba1d90/mdurl-0.1.2.tar.gz"
    sha256 "bb413d29f5eea38f31dd4754dd7377d4465116fb207585f97bf925588687c1ba"
  end

  resource "pygments" do
    url "https://files.pythonhosted.org/packages/49/2e/ced460408999b33da6b31b0021b0f37d329e202d4169aeb164493778f25b/pygments-2.21.0.tar.gz"
    sha256 "610ca751c9bc2492b38eb9a38a7fbc93edbbb2d7182edaf34e66ae493dee5c8c"
  end

  resource "pyyaml" do
    url "https://files.pythonhosted.org/packages/05/8e/961c0007c59b8dd7729d542c61a4d537767a59645b82a0b521206e1e25c2/pyyaml-6.0.3.tar.gz"
    sha256 "d76623373421df22fb4cf8817020cbb7ef15c725b9d5e45f17e189bfc384190f"
  end

  resource "rich" do
    url "https://files.pythonhosted.org/packages/c0/8f/0722ca900cc807c13a6a0c696dacf35430f72e0ec571c4275d2371fca3e9/rich-15.0.0.tar.gz"
    sha256 "edd07a4824c6b40189fb7ac9bc4c52536e9780fbbfbddf6f1e2502c31b068c36"
  end

  def install
    virtualenv_install_with_resources
  end

  test do
    (testpath/"am.yml").write <<~YAML
      route:
        receiver: default
      receivers:
        - name: default
          webhook_configs:
            - url: http://example.com
    YAML
    system bin/"amlint", "check", "am.yml"
  end
end
