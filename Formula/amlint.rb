class Amlint < Formula
  include Language::Python::Virtualenv

  desc "Semantic linter for Prometheus Alertmanager configs"
  homepage "https://github.com/danikdanik2013/amlint"
  url "https://files.pythonhosted.org/packages/source/a/amlint/amlint-0.1.1.tar.gz"
  license "MIT"

  depends_on "python@3.12"

  resource "pyyaml" do
    url "https://files.pythonhosted.org/packages/source/P/PyYAML/PyYAML-6.0.2.tar.gz"
    sha256 "d584d9ec91ad65861cc08d42e834324ef890a082e591037abe114850ff7bbc3e"
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
