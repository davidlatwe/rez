import os


os.environ["REZ_PACKAGES_PATH"] = "memory@any"


def memory_repository(packages):
    from rez.package_repository import package_repository_manager

    repository = package_repository_manager.get_repository("memory@any")
    repository.data = packages


if __name__ == "__main__":
    from rez.packages import create_package
    from rez.developer_package import DeveloperPackage
    from rez.resolved_context import ResolvedContext
    from rez.exceptions import PackageMetadataError

    recipe_repository = {
        "foo": {
            "1": {"name": "foo", "version": "1", "requires": ["bar-*"]},
            "2": {"name": "foo", "version": "2", "requires": ["bar-1.2+"]}
        },
        "bar": {
            "1.1": {"name": "bar", "version": "1.1"},
            "1.2": {"name": "bar", "version": "1.2"},
            "1.3": {"name": "bar", "version": "1.3"},
            "1.4": {"name": "bar", "version": "1.4"},
            "2.0": {"name": "bar", "version": "2.0"},
            "2.1": {"name": "bar", "version": "2.1"},
        },
    }

    name = "foo"
    version = "1"
    data = recipe_repository[name][version]
    package = create_package(name, data, package_cls=DeveloperPackage)

    print("\nExpansion-1")
    print("=" * 20)
    try:
        context = ResolvedContext(["foo-1"], building=True)
    except PackageMetadataError as e:
        raise
    else:
        print(context.resolved_packages)
    finally:
        print(context.success)

    print("\nExpansion-2")
    print("=" * 20)
    try:
        context = ResolvedContext(["foo-2"], building=True)
    except PackageMetadataError as e:
        # print(e)
        pass
    else:
        print(context.resolved_packages)
    finally:
        print(context.success)
