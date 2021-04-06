
from rez.packages import create_package
from rez.developer_package import DeveloperPackage


name = "foo"
data = {
    "version": "1",
    "requires": ["bar-1.*", "bah"]
}

pkg = create_package(name, data, DeveloperPackage)
data = pkg.validated_data()
print(data)
print(data["requires"][0].expansion_directive)
